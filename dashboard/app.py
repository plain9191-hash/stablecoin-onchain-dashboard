import datetime as dt

import pandas as pd
import plotly.express as px
import requests
import streamlit as st

st.set_page_config(page_title="Stablecoin Onchain Dashboard", page_icon="🪙", layout="wide")

CHAINS_API_URL = "https://stablecoins.llama.fi/stablecoinchains"
CHAIN_HISTORY_API = "https://stablecoins.llama.fi/stablecoincharts/{chain}"
TARGET_CHAINS = ["Ethereum", "Tron", "BSC", "Solana"]
DISPLAY_NAME_MAP = {
    "Ethereum": "ETH",
    "Tron": "TRON",
    "BSC": "BNB (BSC)",
    "Solana": "SOL",
}


def _as_datetime(value: str | int | float | None) -> dt.datetime | None:
    if value is None:
        return None
    try:
        ts = int(value)
        if ts > 10_000_000_000:
            ts = int(ts / 1000)
        return dt.datetime.utcfromtimestamp(ts)
    except (TypeError, ValueError, OSError):
        return None


def _extract_pegged_usd(obj: dict | None) -> float | None:
    if not isinstance(obj, dict):
        return None
    value = obj.get("peggedUSD")
    return float(value) if isinstance(value, (int, float)) else None


@st.cache_data(ttl=60 * 30)
def load_chain_supply() -> pd.DataFrame:
    response = requests.get(CHAINS_API_URL, timeout=30)
    response.raise_for_status()
    payload = response.json()

    records: list[dict] = []
    for chain in payload:
        name = chain.get("name")
        if name not in TARGET_CHAINS:
            continue
        market_cap = _extract_pegged_usd(chain.get("totalCirculatingUSD"))
        records.append(
            {
                "chain": name,
                "display_chain": DISPLAY_NAME_MAP.get(name, name),
                "market_cap_usd": market_cap,
            }
        )

    df = pd.DataFrame(records)
    if df.empty:
        return df
    df = df.dropna(subset=["market_cap_usd"]).sort_values("market_cap_usd", ascending=False)
    total = df["market_cap_usd"].sum()
    df["dominance_pct"] = (df["market_cap_usd"] / total * 100) if total else 0.0
    return df


@st.cache_data(ttl=60 * 30)
def load_daily_change(chain_name: str) -> tuple[float | None, float | None, dt.datetime | None]:
    response = requests.get(CHAIN_HISTORY_API.format(chain=chain_name), timeout=30)
    response.raise_for_status()
    rows = response.json()
    if not isinstance(rows, list) or len(rows) < 2:
        return None, None, None

    parsed: list[tuple[dt.datetime, float]] = []
    for row in rows:
        ts = _as_datetime(row.get("date"))
        cap = _extract_pegged_usd(row.get("totalCirculatingUSD"))
        if ts and cap is not None:
            parsed.append((ts, cap))

    if len(parsed) < 2:
        return None, None, None

    parsed.sort(key=lambda x: x[0])
    latest_ts, latest_val = parsed[-1]
    prev_val = parsed[-2][1]
    delta_abs = latest_val - prev_val
    delta_pct = (delta_abs / prev_val * 100) if prev_val else None
    return delta_abs, delta_pct, latest_ts


def fmt_usd(value: float | None) -> str:
    if value is None or pd.isna(value):
        return "-"
    if value >= 1_000_000_000:
        return f"${value / 1_000_000_000:,.1f}B"
    if value >= 1_000_000:
        return f"${value / 1_000_000:,.1f}M"
    return f"${value:,.0f}"


st.title("Stablecoin Onchain Dashboard")
st.caption("Chains: ETH, TRON, BNB (BSC), SOL | Public, no-login dashboard")

with st.sidebar:
    st.header("Controls")
    selected = st.multiselect(
        "Select chains",
        options=TARGET_CHAINS,
        default=TARGET_CHAINS,
        format_func=lambda x: DISPLAY_NAME_MAP.get(x, x),
    )
    st.caption("Source: DefiLlama stablecoin APIs")

try:
    base_df = load_chain_supply()
except requests.RequestException as exc:
    st.error(f"Failed to load onchain data: {exc}")
    st.stop()

if base_df.empty:
    st.warning("No chain data found from the API.")
    st.stop()

view_df = base_df[base_df["chain"].isin(selected)].copy()
if view_df.empty:
    st.warning("Please select at least one chain.")
    st.stop()

changes = view_df["chain"].apply(load_daily_change)
view_df["delta_abs_usd"] = changes.apply(lambda x: x[0])
view_df["delta_pct"] = changes.apply(lambda x: x[1])
view_df["last_updated"] = changes.apply(lambda x: x[2])

col1, col2, col3 = st.columns(3)
col1.metric("Total Stablecoin Supply", fmt_usd(view_df["market_cap_usd"].sum()))
col2.metric("Top Chain", view_df.iloc[0]["display_chain"])
latest_update = view_df["last_updated"].dropna()
col3.metric(
    "Last Updated (UTC)",
    latest_update.max().strftime("%Y-%m-%d") if not latest_update.empty else "-",
)

chart_col1, chart_col2 = st.columns(2)

with chart_col1:
    fig_bar = px.bar(
        view_df,
        x="display_chain",
        y="market_cap_usd",
        color="display_chain",
        labels={"display_chain": "Chain", "market_cap_usd": "Stablecoin supply (USD)"},
        title="Stablecoin Supply by Chain",
    )
    fig_bar.update_layout(showlegend=False)
    st.plotly_chart(fig_bar, use_container_width=True)

with chart_col2:
    fig_pie = px.pie(
        view_df,
        names="display_chain",
        values="market_cap_usd",
        title="Chain Dominance",
        hole=0.45,
    )
    st.plotly_chart(fig_pie, use_container_width=True)

st.subheader("Chain Snapshot")
table_df = view_df[
    ["display_chain", "market_cap_usd", "dominance_pct", "delta_abs_usd", "delta_pct", "last_updated"]
].rename(
    columns={
        "display_chain": "Chain",
        "market_cap_usd": "Supply (USD)",
        "dominance_pct": "Dominance (%)",
        "delta_abs_usd": "Change (1d USD)",
        "delta_pct": "Change (1d %)",
        "last_updated": "Last Updated (UTC)",
    }
)

st.dataframe(
    table_df,
    hide_index=True,
    use_container_width=True,
    column_config={
        "Supply (USD)": st.column_config.NumberColumn(format="$%.0f"),
        "Dominance (%)": st.column_config.NumberColumn(format="%.2f"),
        "Change (1d USD)": st.column_config.NumberColumn(format="$%.0f"),
        "Change (1d %)": st.column_config.NumberColumn(format="%.2f%%"),
        "Last Updated (UTC)": st.column_config.DatetimeColumn(format="YYYY-MM-DD"),
    },
)
