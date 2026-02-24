import datetime as dt
from typing import Dict, List

import pandas as pd
import plotly.express as px
import requests
import streamlit as st

st.set_page_config(page_title="Stablecoin Onchain Dashboard", page_icon="🪙", layout="wide")

API_URL = "https://stablecoins.llama.fi/stablecoinchains"
TARGET_CHAINS = ["Ethereum", "Tron", "BSC", "Solana"]
DISPLAY_NAME_MAP = {
    "Ethereum": "ETH",
    "Tron": "TRON",
    "BSC": "BNB (BSC)",
    "Solana": "SOL",
}


def _as_datetime(value) -> dt.datetime | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        try:
            if value > 10_000_000_000:
                return dt.datetime.utcfromtimestamp(value / 1000)
            return dt.datetime.utcfromtimestamp(value)
        except (ValueError, OSError):
            return None
    if isinstance(value, str):
        try:
            return dt.datetime.fromisoformat(value.replace("Z", "+00:00")).replace(tzinfo=None)
        except ValueError:
            return None
    return None


def _extract_recent(values) -> Dict[str, float | dt.datetime | None]:
    if not values:
        return {"latest": None, "prev": None, "last_updated": None}

    rows: List[tuple[dt.datetime, float]] = []
    for item in values:
        if isinstance(item, dict):
            ts = _as_datetime(item.get("date") or item.get("timestamp") or item.get("time"))
            cap = item.get("totalCirculatingUSD")
            if cap is None:
                cap = item.get("totalCirculating", {}).get("peggedUSD") if isinstance(item.get("totalCirculating"), dict) else None
            if cap is None:
                cap = item.get("marketCap")
            if ts and isinstance(cap, (int, float)):
                rows.append((ts, float(cap)))
        elif isinstance(item, (list, tuple)) and len(item) >= 2:
            ts = _as_datetime(item[0])
            cap = item[1]
            if ts and isinstance(cap, (int, float)):
                rows.append((ts, float(cap)))

    if not rows:
        return {"latest": None, "prev": None, "last_updated": None}

    rows.sort(key=lambda x: x[0])
    latest_ts, latest_val = rows[-1]
    prev_val = rows[-2][1] if len(rows) > 1 else None
    return {"latest": latest_val, "prev": prev_val, "last_updated": latest_ts}


@st.cache_data(ttl=60 * 30)
def load_chain_data() -> pd.DataFrame:
    response = requests.get(API_URL, timeout=30)
    response.raise_for_status()
    payload = response.json()

    chains = payload if isinstance(payload, list) else payload.get("chains", [])
    records = []

    for chain in chains:
        name = chain.get("name")
        if name not in TARGET_CHAINS:
            continue

        recent = _extract_recent(chain.get("chainBalances") or chain.get("history") or chain.get("marketCaps") or [])

        latest = recent["latest"]
        prev = recent["prev"]
        delta_abs = (latest - prev) if (latest is not None and prev is not None) else None
        delta_pct = ((latest - prev) / prev * 100) if (latest is not None and prev and prev != 0) else None

        records.append(
            {
                "chain": name,
                "display_chain": DISPLAY_NAME_MAP.get(name, name),
                "market_cap_usd": latest,
                "delta_abs_usd": delta_abs,
                "delta_pct": delta_pct,
                "last_updated": recent["last_updated"],
            }
        )

    df = pd.DataFrame(records)
    if not df.empty:
        df = df.sort_values("market_cap_usd", ascending=False)
        total = df["market_cap_usd"].sum()
        df["dominance_pct"] = (df["market_cap_usd"] / total * 100) if total else 0.0
    return df


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
    st.caption("Data source: DefiLlama stablecoinchains API")

try:
    base_df = load_chain_data()
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

col1, col2, col3 = st.columns(3)
col1.metric("Total Stablecoin Supply", fmt_usd(view_df["market_cap_usd"].sum()))
col2.metric("Top Chain", view_df.iloc[0]["display_chain"])
latest_update = view_df["last_updated"].dropna()
col3.metric(
    "Last Updated (UTC)",
    latest_update.max().strftime("%Y-%m-%d %H:%M") if not latest_update.empty else "-",
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
        "delta_abs_usd": "Change (USD)",
        "delta_pct": "Change (%)",
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
        "Change (USD)": st.column_config.NumberColumn(format="$%.0f"),
        "Change (%)": st.column_config.NumberColumn(format="%.2f%%"),
        "Last Updated (UTC)": st.column_config.DatetimeColumn(format="YYYY-MM-DD HH:mm"),
    },
)
