# Penjaga for management (portal)

The township management office's side of Penjaga (PRD §17):

- **Reports from homes**: reports homeowners chose to send from a case arrive here with the problem, where it is and
  the evidence. Staff move a report from received to scheduled, in progress and resolved, and reply; each change
  appears on the homeowner's case timeline straight away.
- **Notices to homes**: staff paste a utility's notice (Malay or English); Penjaga's AI reads the dates, areas and
  recovery time into fields; staff check them and publish. Homes in the areas named see the notice with what it means
  for their house, and a supply drop during the works is explained as planned, not a fault. Fields can also be filled
  in by hand (no AI).

```bash
npm install
npm run dev        # http://localhost:5181, with the Core API on :8705
npm run build      # type-check and build
```

- The Vite dev proxy forwards `/api` to the Core API and adds the **management** token on the server side
  (`PENJAGA_MANAGEMENT_TOKEN`, or the Core API's `var/api-tokens.json`), so the browser never holds a token.
- `?ticket=<id>` opens a report directly; `?page=notices` opens the notices page; `?theme=dark|light` picks the
  theme (also a toggle in the sidebar).
- No SDP, Air Selangor or TNB branding.
- Design: the homeowner app's family (paper, deep green, Fraunces + Hanken Grotesk) as a work surface; colour tokens
  approved by the owner, light and dark.
