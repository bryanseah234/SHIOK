# Lighthouse Accessibility QA - 2026-08-02

Scope: local production Next build served at `http://127.0.0.1:3100/`.
This checks the initial map/search page only. It does not replace a full
mobile screenshot review or keyboard-only routed-search walkthrough.

Command:

```powershell
npx --yes lighthouse http://127.0.0.1:3100/ --only-categories=accessibility --output=json --output-path=logs\lighthouse-a11y-20260802.json --chrome-flags="--headless --disable-gpu --no-sandbox" --quiet
```

Result:

```json
{
  "accessibility_score": 100,
  "failed_audits": []
}
```

Summary artifact: `qa/lighthouse_a11y_20260802_summary.json`
