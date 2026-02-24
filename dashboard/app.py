import datetime as dt

import pandas as pd
import plotly.express as px
import requests
import streamlit as st

st.set_page_config(page_title="Stablecoin Onchain Dashboard", page_icon="🪙", layout="wide")

STABLECOINS_API_URL = "https://stablecoins.llama.fi/stablecoins?includePrices=true"
FX_API_URL = "https://api.frankfurter.app/latest?from=USD&to=KRW"
TARGET_CHAINS = ["Ethereum", "Tron", "BSC", "Solana"]
DISPLAY_NAME_MAP = {
    "Ethereum": "ETH",
    "Tron": "TRON",
    "BSC": "BNB (BSC)",
    "Solana": "SOL",
}
CHAIN_SCOPE_OPTIONS = ["전체", "ETH", "TRON", "BNB", "SOL"]
CHAIN_SCOPE_TO_CHAIN = {
    "전체": None,
    "ETH": "Ethereum",
    "TRON": "Tron",
    "BNB": "BSC",
    "SOL": "Solana",
}
TOKEN_OPTIONS = ["전체", "USDT", "USDC", "DAI", "USDe", "FDUSD", "PYUSD"]


def _extract_pegged_usd(obj: dict | None) -> float:
    if not isinstance(obj, dict):
        return 0.0
    value = obj.get("peggedUSD")
    return float(value) if isinstance(value, (int, float)) else 0.0


@st.cache_data(ttl=60 * 30)
def load_stablecoin_assets() -> tuple[list[dict], str]:
    response = requests.get(STABLECOINS_API_URL, timeout=30)
    response.raise_for_status()
    payload = response.json()
    assets = payload.get("peggedAssets", []) if isinstance(payload, dict) else []
    fetched_at = dt.datetime.utcnow().strftime("%Y-%m-%d %H:%M")
    return assets, fetched_at


@st.cache_data(ttl=60 * 60)
def load_usd_krw_rate() -> float | None:
    response = requests.get(FX_API_URL, timeout=20)
    response.raise_for_status()
    payload = response.json()
    rates = payload.get("rates", {}) if isinstance(payload, dict) else {}
    krw = rates.get("KRW")
    return float(krw) if isinstance(krw, (int, float)) else None


def build_chain_dataframe(assets: list[dict], token_symbol: str) -> pd.DataFrame:
    agg = {
        chain: {
            "chain": chain,
            "display_chain": DISPLAY_NAME_MAP.get(chain, chain),
            "market_cap_usd": 0.0,
            "prev_day_usd": 0.0,
        }
        for chain in TARGET_CHAINS
    }

    for asset in assets:
        if asset.get("pegType") != "peggedUSD":
            continue
        symbol = asset.get("symbol")
        if token_symbol != "전체" and symbol != token_symbol:
            continue

        chain_circulating = asset.get("chainCirculating", {})
        if not isinstance(chain_circulating, dict):
            continue

        for chain in TARGET_CHAINS:
            chain_data = chain_circulating.get(chain, {})
            if not isinstance(chain_data, dict):
                continue
            agg[chain]["market_cap_usd"] += _extract_pegged_usd(chain_data.get("current"))
            agg[chain]["prev_day_usd"] += _extract_pegged_usd(chain_data.get("circulatingPrevDay"))

    df = pd.DataFrame(list(agg.values()))
    df["delta_abs_usd"] = df["market_cap_usd"] - df["prev_day_usd"]
    df["delta_pct"] = df.apply(
        lambda r: (r["delta_abs_usd"] / r["prev_day_usd"] * 100) if r["prev_day_usd"] else None,
        axis=1,
    )

    total = df["market_cap_usd"].sum()
    df["dominance_pct"] = (df["market_cap_usd"] / total * 100) if total else 0.0
    return df.sort_values("market_cap_usd", ascending=False)


def fmt_usd_compact(value: float | None) -> str:
    if value is None or pd.isna(value):
        return "-"
    if value >= 1_000_000_000:
        return f"${value / 1_000_000_000:,.1f}B"
    if value >= 1_000_000:
        return f"${value / 1_000_000:,.1f}M"
    return f"${value:,.0f}"


def fmt_usd_full(value: float | None) -> str:
    if value is None or pd.isna(value):
        return "-"
    return f"${value:,.0f}"


def fmt_krw_full(value: float | None) -> str:
    if value is None or pd.isna(value):
        return "-"
    return f"₩{value:,.0f}"


def fmt_pct(value: float | None) -> str:
    if value is None or pd.isna(value):
        return "-"
    return f"{value:,.2f}%"


st.title("Stablecoin Onchain Dashboard")
st.caption("Public, no-login dashboard | Chain + stablecoin type filters")

with st.sidebar:
    st.header("Controls")
    chain_scope = st.selectbox("Chain scope", CHAIN_SCOPE_OPTIONS, index=0)
    token_scope = st.selectbox("STC type", TOKEN_OPTIONS, index=0)
    st.caption("Source: DefiLlama stablecoin APIs")

try:
    assets, fetched_at = load_stablecoin_assets()
except requests.RequestException as exc:
    st.error(f"Failed to load onchain data: {exc}")
    st.stop()

base_df = build_chain_dataframe(assets, token_scope)
selected_chain = CHAIN_SCOPE_TO_CHAIN[chain_scope]
view_df = base_df if selected_chain is None else base_df[base_df["chain"] == selected_chain].copy()

if view_df.empty:
    st.warning("No data for the selected filter.")
    st.stop()

usd_krw_rate = None
try:
    usd_krw_rate = load_usd_krw_rate()
except requests.RequestException:
    usd_krw_rate = None

view_df["market_cap_krw"] = view_df["market_cap_usd"] * usd_krw_rate if usd_krw_rate else None

col1, col2, col3 = st.columns(3)
col1.metric("Total Stablecoin Supply", fmt_usd_compact(view_df["market_cap_usd"].sum()))
col2.metric("Top Chain", view_df.iloc[0]["display_chain"])
col3.metric("Data Fetched (UTC)", fetched_at)

if usd_krw_rate is not None:
    st.caption(f"FX Rate: 1 USD = {usd_krw_rate:,.2f} KRW")
else:
    st.caption("FX Rate: unavailable")

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
    [
        "display_chain",
        "market_cap_usd",
        "market_cap_krw",
        "dominance_pct",
        "delta_abs_usd",
        "delta_pct",
    ]
].rename(
    columns={
        "display_chain": "Chain",
        "market_cap_usd": "Supply (USD)",
        "market_cap_krw": "Supply (KRW)",
        "dominance_pct": "Dominance (%)",
        "delta_abs_usd": "Change (1d USD)",
        "delta_pct": "Change (1d %)",
    }
)

snapshot_df = table_df.copy()
snapshot_df["Supply (USD)"] = snapshot_df["Supply (USD)"].map(fmt_usd_full)
snapshot_df["Supply (KRW)"] = snapshot_df["Supply (KRW)"].map(fmt_krw_full)
snapshot_df["Change (1d USD)"] = snapshot_df["Change (1d USD)"].map(fmt_usd_full)
snapshot_df["Dominance (%)"] = snapshot_df["Dominance (%)"].map(fmt_pct)
snapshot_df["Change (1d %)"] = snapshot_df["Change (1d %)"].map(fmt_pct)

st.dataframe(snapshot_df, hide_index=True, use_container_width=True)
