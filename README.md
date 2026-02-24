# Stablecoin PM Toolkit

This repository contains two separated products:

- `dashboard/`: public no-login Streamlit onchain dashboard (ETH/TRON/BNB/SOL)
- `newsletter/`: separate daily newsletter workflow

## 1) Dashboard (public URL)

```bash
cd dashboard
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
streamlit run app.py
```

Deploy to Streamlit Community Cloud and share the generated app URL.

## 2) Newsletter (separate service)

```bash
cd newsletter
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python oauth_setup.py
python newsletter_workflow.py
```

Newsletter should send the dashboard URL in email as a separate workflow.
