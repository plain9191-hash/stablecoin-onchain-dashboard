# Dashboard (Streamlit)

Public no-login stablecoin onchain dashboard.

## Local run

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
streamlit run app.py
```

## Deploy

Use Streamlit Community Cloud:
- Repository: this repo
- Main file path: `dashboard/app.py`
- Requirements path: `dashboard/requirements.txt`
