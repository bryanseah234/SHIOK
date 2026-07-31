# Lighthouse Accessibility QA - 2026-08-01

Command:

```powershell
npx --yes lighthouse https://sgshiok.vercel.app --only-categories=accessibility --chrome-flags="--headless=new --no-sandbox" --output=json --output-path=qa\lighthouse_accessibility_20260801.json ; echo "exit=$LASTEXITCODE"
```

Result:

- exit: 0
- requested URL: `https://sgshiok.vercel.app/`
- final URL: `https://sgshiok.vercel.app/`
- accessibility score: 100/100
- failing accessibility audits: 0

Raw report:

- `qa/lighthouse_accessibility_20260801.json`
