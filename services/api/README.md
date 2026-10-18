# penjaga-api: Core API

The service in the middle of the architecture (PRD §9.1). It follows the detection service's findings, keeps
what the homeowner app, the management portal and the Lab need, and serves them on `/api/v1`.

```bash
uv run python scripts/dev_stack.py        # stream, simulator, detection, evaluation, and this API (port 8705)
uv run penjaga-api serve --dev-tokens var/dev-tokens.json   # this API on its own
```

- **Upstream:** it talks to detection (`/detect/v1`) and the stream service (`/stream/v1`) over HTTP only, with
  the **app** role token. It never calls the simulator and never sees ground truth (import rules enforce it).
- **Callers:** roles `homeowner`, `management` and `operator`, with one bearer token each. In development they come
  from `var/api-tokens.json` (created on first run) and the operator token from `var/dev-tokens.json`.
- **House time:** every timestamp it writes is house time, read from the stream service's clock.
- **Personal data:** the store holds data linked to a house address, which is personal data under the amended
  PDPA. It keeps a consent record per layer, a disclosure log, and retention settings per data class.

## Cases (step 3)

Findings become **cases** in the shape the homeowner app renders (`Case` in the app's `src/api/types.ts`):
`GET /api/v1/view` returns the whole `View` (cases, home summary, consent, tour flag), and `GET /api/v1/cases[/{id}]`,
`POST /api/v1/cases/{id}/dismiss` and `POST /api/v1/toured` cover the rest. Changes go out as `case` events on
`/api/v1/events`.

- `cases.py` groups findings into cases (one per underlying element) and replays their history into a status, a
  severity and a timeline. A safety-floor finding never shows lower than its highest severity while it is active, and
  an urgent or safety-floor case can't be dismissed.
- `templates.py` writes each case from the finding's own numbers and the house record's names: one template per
  finding type (all 16), no raw IDs, the trade terms "ST-registered electrical contractor" and "SPAN-registered
  plumber". Urgent cases show the reviewed text in `data/safety_guidance.json`. This is the LLM-down path.
- Evidence charts come from `/stream/v1/readings/summary`; today's usage is compared with the same hours on earlier
  days of the house's own history (no typical value until there is one).

## Knowledge base and cost of waiting (step 4)

- `data/tariffs/*.json`: TNB domestic (RP4) and Air Selangor domestic tariffs as versioned records, each with sources,
  the date checked, a confidence level and its effective period. `tariffs.py` computes bills line by line and reproduces
  the Energy Commission's sample bill (RM380.99 for 801 kWh) to the sen.
- `costs.py`: the cost of waiting is always `bill(usual + extra) - bill(usual)`, with the household's usual month measured
  from its own readings before the problem began. Electricity is averaged over usual months 50 kWh either side, because
  the tariff steps every 50 kWh; the big steps (the protection threshold, 1,500 kWh) get a note.
- `data/safety_guidance.json`: the three safety texts with their official sources (or marked as Penjaga's own wording).
- `data/vendors.json` and `GET /api/v1/vendors?category=`: the illustrative vendor directory.
- `knowledge.py`: the same facts as agent tools (step 6).

## AI explanations and "Ask Penjaga" (step 6)

Put `OPENROUTER_API_KEY` in the backend's `.env` (see `.env.example`); without it everything still works on templates.

- `agent/openrouter.py`: plain-HTTP client with the privacy settings on every request and the documented error handling.
- `agent/explain.py`: case explanations as strict JSON, written in the background and stored per set of facts.
- `agent/chat.py` + `agent/tools.py`: the tool loop behind `POST /api/v1/chat` (SSE) and `GET /api/v1/chat/history`.
- `agent/guardrails.py`: no raw IDs, no numbers missing from the facts, no playing down serious cases, correct trade terms.
- Consent layer `AI` (`PUT /api/v1/consent/AI`) switches all of it off; every LLM call is in the disclosure log.
- `uv run penjaga-api eval-agent --models <model> <model>` scores candidate models live (uses the key).

## Actions and the management side (step 7)

- `actions.py`: tickets to management, vendor bookings and guided checks. Each needs a single-use confirmation:
  `POST /api/v1/cases/{id}/actions/{ticket|booking|check}/preview` shows what would be sent and to whom, and returns
  the token the action route requires. What happened goes on the case timeline.
- `GET/POST /api/v1/manage/tickets…` (management role) is the portal's inbox: status and replies.

## Notices and maintenance (step 8)

- `notices.py`, `data/notice_presets.json`: management notices. `POST /api/v1/manage/notices/parse` reads a pasted
  notice into fields with the AI (`agent/notices.py`); `POST /api/v1/manage/notices` publishes what staff confirmed;
  `GET /api/v1/manage/notices` and `…/{id}/withdraw` manage them; `POST /api/v1/notices/seed` (operator) posts a preset
  adapted from a past public notice. Notices for this house's area are in `view.notices` with what they mean for it,
  and a water or power drop inside a notice's window is explained as planned works.
- `maintenance.py`, `data/maintenance_rules.json`: reminders from sourced rules (source, confidence, conflicting
  advice) counted from install dates and completions. `view.maintenance` lists the soonest; `GET /api/v1/maintenance`
  has all; `POST /api/v1/maintenance/{id}/done` records one. `notice` and `maintenance` events go out on
  `/api/v1/events`.
