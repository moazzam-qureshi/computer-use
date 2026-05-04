=== Extreme zoom-out diagnostic ===
Tests how aggressively we can zoom while keeping UIA + click + panel-walk working.

[setup] refresh feed (one-time)
[act] 02:43:42  fg='Upwork - Google Chrome'  key 'ctrl+t'
[act] 02:43:43  fg='New tab - Google Chrome'  key 'ctrl+l'
[act] 02:43:47  fg='New tab - Google Chrome'  key 'enter'

============================================================
  ZOOM LEVEL: 67%  (Ctrl+- x4)
============================================================
[act] 02:44:08  fg='Upwork - Google Chrome'  key 'ctrl+home'

  [walk A] top of feed
    cards visible: 2, 'Posted' anchors: 2, walk: 3.80s
      1. AI Prompting & Automation Specialist (Claude · Gemini · ChatGPT) with n8n experi
      2. Senior Node.js Engineer — On-Call Maintenance for AI Document Pipeline (Ongoing,

  [walk B] after Down x21
[act] 02:44:18  fg='Upwork - Google Chrome'  key 'down'
[act] 02:44:18  fg='Upwork - Google Chrome'  key 'down'
[act] 02:44:19  fg='Upwork - Google Chrome'  key 'down'
[act] 02:44:19  fg='Upwork - Google Chrome'  key 'down'
[act] 02:44:19  fg='Upwork - Google Chrome'  key 'down'
[act] 02:44:19  fg='Upwork - Google Chrome'  key 'down'
[act] 02:44:20  fg='Upwork - Google Chrome'  key 'down'
[act] 02:44:20  fg='Upwork - Google Chrome'  key 'down'
[act] 02:44:22  fg='Upwork - Google Chrome'  key 'down'
[act] 02:44:22  fg='Upwork - Google Chrome'  key 'down'
[act] 02:44:22  fg='Upwork - Google Chrome'  key 'down'
[act] 02:44:22  fg='Upwork - Google Chrome'  key 'down'
[act] 02:44:23  fg='Upwork - Google Chrome'  key 'down'
[act] 02:44:23  fg='Upwork - Google Chrome'  key 'down'
[act] 02:44:23  fg='Upwork - Google Chrome'  key 'down'
[act] 02:44:27  fg='Upwork - Google Chrome'  key 'down'
[act] 02:44:27  fg='Upwork - Google Chrome'  key 'down'
[act] 02:44:28  fg='Upwork - Google Chrome'  key 'down'
[act] 02:44:28  fg='Upwork - Google Chrome'  key 'down'
[act] 02:44:29  fg='Upwork - Google Chrome'  key 'down'
[act] 02:44:29  fg='Upwork - Google Chrome'  key 'down'
    cards visible: 4, new since A: 4
      +1. AI Automations Developer Needed for Innovative Projects
      +2. AI Engineer (Chat + Voice + Memory) for Monetized AI Companion Platform (Open AI
      +3. AI Technical Architect / Advisor
      +4. Full-Stack Engineer (Contract) — AI-Native Internal Tools & Products

  [walk C] after another Down x21
[act] 02:44:33  fg='Upwork - Google Chrome'  key 'down'
[act] 02:44:33  fg='Upwork - Google Chrome'  key 'down'
[act] 02:44:34  fg='Upwork - Google Chrome'  key 'down'
[act] 02:44:34  fg='Upwork - Google Chrome'  key 'down'
[act] 02:44:34  fg='Upwork - Google Chrome'  key 'down'
[act] 02:44:34  fg='Upwork - Google Chrome'  key 'down'
[act] 02:44:35  fg='Upwork - Google Chrome'  key 'down'
[act] 02:44:35  fg='Upwork - Google Chrome'  key 'down'
[act] 02:44:35  fg='Upwork - Google Chrome'  key 'down'
[act] 02:44:35  fg='Upwork - Google Chrome'  key 'down'
[act] 02:44:35  fg='Upwork - Google Chrome'  key 'down'
[act] 02:44:37  fg='Upwork - Google Chrome'  key 'down'
[act] 02:44:37  fg='Upwork - Google Chrome'  key 'down'
[act] 02:44:38  fg='Upwork - Google Chrome'  key 'down'
[act] 02:44:38  fg='Upwork - Google Chrome'  key 'down'
[act] 02:44:38  fg='Upwork - Google Chrome'  key 'down'
[act] 02:44:38  fg='Upwork - Google Chrome'  key 'down'
[act] 02:44:38  fg='Upwork - Google Chrome'  key 'down'
[act] 02:44:39  fg='Upwork - Google Chrome'  key 'down'
[act] 02:44:39  fg='Upwork - Google Chrome'  key 'down'
[act] 02:44:39  fg='Upwork - Google Chrome'  key 'down'
    cards visible: 4, new since A+B: 3

  TOTAL UNIQUE CARDS at this zoom: 9
[act] 02:44:44  fg='Upwork - Google Chrome'  key 'ctrl+home'

  [panel-test] picked first card: 'AI Prompting & Automation Specialist (Claude · Gemini · Chat'
    bounds=(430, 616, 1118, 637)  mid_y=626  width=688px  height=21px
    clicking...
[act] 02:44:48  fg='Upwork - Google Chrome'  click name='AI Prompting & Automation Specialist (Cl' bounds=(430, 616, 1118, 637) target_xy=(716,625)
    capturing panel...
    panel: 0 elements, url=''
    parsed: budget_kind=None  budget_min=None  country=None
            description chars: 0
            skills count: 0
[act] 02:44:54  fg='Upwork - Google Chrome'  key 'escape'

============================================================
  ZOOM LEVEL: 50%  (Ctrl+- x5)
============================================================
[act] 02:44:56  fg='Upwork - Google Chrome'  key 'ctrl+home'

  [walk A] top of feed
    cards visible: 3, 'Posted' anchors: 4, walk: 4.39s
      1. AI Prompting & Automation Specialist (Claude · Gemini · ChatGPT) with n8n experi
      2. Senior Node.js Engineer — On-Call Maintenance for AI Document Pipeline (Ongoing,
      3. AI Automations Developer Needed for Innovative Projects

  [walk B] after Down x21
[act] 02:45:07  fg='Upwork - Google Chrome'  key 'down'
[act] 02:45:09  fg='Upwork - Google Chrome'  key 'down'
[act] 02:45:09  fg='Upwork - Google Chrome'  key 'down'
[act] 02:45:09  fg='Upwork - Google Chrome'  key 'down'
[act] 02:45:09  fg='Upwork - Google Chrome'  key 'down'
[act] 02:45:10  fg='Upwork - Google Chrome'  key 'down'
[act] 02:45:10  fg='Upwork - Google Chrome'  key 'down'
[act] 02:45:10  fg='Upwork - Google Chrome'  key 'down'
[act] 02:45:10  fg='Upwork - Google Chrome'  key 'down'
[act] 02:45:10  fg='Upwork - Google Chrome'  key 'down'
[act] 02:45:11  fg='Upwork - Google Chrome'  key 'down'
[act] 02:45:11  fg='Upwork - Google Chrome'  key 'down'
[act] 02:45:11  fg='Upwork - Google Chrome'  key 'down'
[act] 02:45:12  fg='Upwork - Google Chrome'  key 'down'
[act] 02:45:12  fg='Upwork - Google Chrome'  key 'down'
[act] 02:45:12  fg='Upwork - Google Chrome'  key 'down'
[act] 02:45:13  fg='Upwork - Google Chrome'  key 'down'
[act] 02:45:13  fg='Upwork - Google Chrome'  key 'down'
[act] 02:45:13  fg='Upwork - Google Chrome'  key 'down'
[act] 02:45:14  fg='Upwork - Google Chrome'  key 'down'
[act] 02:45:15  fg='Upwork - Google Chrome'  key 'down'
    cards visible: 4, new since A: 4
      +1. AI Technical Architect / Advisor
      +2. Full-Stack Engineer (Contract) — AI-Native Internal Tools & Products
      +3. AI Voice Agent Using Retell AI
      +4. Claude Code Power User — Production Bot Hardening (Python + Automation)

  [walk C] after another Down x21
[act] 02:45:21  fg='Upwork - Google Chrome'  key 'down'
[act] 02:45:21  fg='Upwork - Google Chrome'  key 'down'
[act] 02:45:21  fg='Upwork - Google Chrome'  key 'down'
[act] 02:45:23  fg='Upwork - Google Chrome'  key 'down'
[act] 02:45:23  fg='Upwork - Google Chrome'  key 'down'
[act] 02:45:24  fg='Upwork - Google Chrome'  key 'down'
[act] 02:45:24  fg='Upwork - Google Chrome'  key 'down'
[act] 02:45:25  fg='Upwork - Google Chrome'  key 'down'
[act] 02:45:26  fg='Upwork - Google Chrome'  key 'down'
[act] 02:45:27  fg='Upwork - Google Chrome'  key 'down'
[act] 02:45:27  fg='Upwork - Google Chrome'  key 'down'
[act] 02:45:29  fg='Upwork - Google Chrome'  key 'down'
[act] 02:45:30  fg='Upwork - Google Chrome'  key 'down'
[act] 02:45:30  fg='Upwork - Google Chrome'  key 'down'
[act] 02:45:30  fg='Upwork - Google Chrome'  key 'down'
[act] 02:45:31  fg='Upwork - Google Chrome'  key 'down'
[act] 02:45:31  fg='Upwork - Google Chrome'  key 'down'
[act] 02:45:31  fg='Upwork - Google Chrome'  key 'down'
[act] 02:45:31  fg='Upwork - Google Chrome'  key 'down'
[act] 02:45:32  fg='Upwork - Google Chrome'  key 'down'
[act] 02:45:32  fg='Upwork - Google Chrome'  key 'down'
    cards visible: 3, new since A+B: 2

  TOTAL UNIQUE CARDS at this zoom: 8
[act] 02:45:38  fg='Upwork - Google Chrome'  key 'ctrl+home'

  [panel-test] picked first card: 'AI Prompting & Automation Specialist (Claude · Gemini · Chat'
    bounds=(560, 489, 1076, 505)  mid_y=497  width=516px  height=16px
    clicking...
[act] 02:45:43  fg='Upwork - Google Chrome'  click name='AI Prompting & Automation Specialist (Cl' bounds=(560, 489, 1076, 505) target_xy=(780,497)
    capturing panel...
[panel] Copy visible at top; clicking now bounds=(1734, 649, 1772, 662)
[act] 02:45:51  fg='Upwork - Google Chrome'  click_xy (1753,655)
[panel] URL captured: https://www.upwork.com/jobs/~022051407361763230919
[panel] reached end of panel after 4 press(es); merged 406 elements; url=YES
    panel: 406 elements, url='https://www.upwork.com/jobs/~022051407361763230919'
[panel.parse] budget extraction trail: ["HOURLY@47 <- 'Hourly'", "MONEY@55 <- '$15.00'", "MONEY@56 <- '$20.00'", "HOURLY@57 <- 'Hourly'", "MONEY@110 <- '$9.29'", "MONEY@169 <- '$15.00'", "MONEY@170 <- '$20.00'", "HOURLY@171 <- 'Hourly'", "FIXED@222 <- 'Fixed-price'", "MONEY@223 <- '$300.00'", "FIXED@236 <- 'Fixed-price'", "FIXED@248 <- 'Fixed-price'", "FIXED@324 <- 'Fixed-price'", "MONEY@325 <- '$300.00'", "FIXED@338 <- 'Fixed-price'", "FIXED@350 <- 'Fixed-price'", "HOURLY@386 <- 'Hourly'", "HOURLY@390 <- 'Hourly'", "HOURLY@394 <- 'Hourly'", "HOURLY@398 <- 'Hourly'", "HOURLY@402 <- 'Hourly'"] -> kind='hourly'
    parsed: budget_kind=hourly  budget_min=15.0  country=United States
            description chars: 1349
            skills count: 5
[act] 02:46:04  fg='Upwork - Google Chrome'  key 'escape'

============================================================
  ZOOM LEVEL: 33%  (Ctrl+- x6)
============================================================
[act] 02:46:06  fg='Upwork - Google Chrome'  key 'ctrl+home'

  [walk A] top of feed
    cards visible: 6, 'Posted' anchors: 6, walk: 5.27s
      1. AI Prompting & Automation Specialist (Claude · Gemini · ChatGPT) with n8n experi
      2. Senior Node.js Engineer — On-Call Maintenance for AI Document Pipeline (Ongoing,
      3. AI Automations Developer Needed for Innovative Projects
      4. AI Engineer (Chat + Voice + Memory) for Monetized AI Companion Platform (Open AI
      5. AI Technical Architect / Advisor
      6. Full-Stack Engineer (Contract) — AI-Native Internal Tools & Products

  [walk B] after Down x21
[act] 02:46:18  fg='Upwork - Google Chrome'  key 'down'
[act] 02:46:18  fg='Upwork - Google Chrome'  key 'down'
[act] 02:46:18  fg='Upwork - Google Chrome'  key 'down'
[act] 02:46:19  fg='Upwork - Google Chrome'  key 'down'
[act] 02:46:21  fg='Upwork - Google Chrome'  key 'down'
[act] 02:46:21  fg='Upwork - Google Chrome'  key 'down'
[act] 02:46:21  fg='Upwork - Google Chrome'  key 'down'
[act] 02:46:21  fg='Upwork - Google Chrome'  key 'down'
[act] 02:46:21  fg='Upwork - Google Chrome'  key 'down'
[act] 02:46:22  fg='Upwork - Google Chrome'  key 'down'
[act] 02:46:22  fg='Upwork - Google Chrome'  key 'down'
[act] 02:46:22  fg='Upwork - Google Chrome'  key 'down'
[act] 02:46:23  fg='Upwork - Google Chrome'  key 'down'
[act] 02:46:23  fg='Upwork - Google Chrome'  key 'down'
[act] 02:46:23  fg='Upwork - Google Chrome'  key 'down'
[act] 02:46:23  fg='Upwork - Google Chrome'  key 'down'
[act] 02:46:23  fg='Upwork - Google Chrome'  key 'down'
[act] 02:46:24  fg='Upwork - Google Chrome'  key 'down'
[act] 02:46:24  fg='Upwork - Google Chrome'  key 'down'
[act] 02:46:25  fg='Upwork - Google Chrome'  key 'down'
[act] 02:46:25  fg='Upwork - Google Chrome'  key 'down'
    cards visible: 5, new since A: 4
      +1. AI Voice Agent Using Retell AI
      +2. Claude Code Power User — Production Bot Hardening (Python + Automation)
      +3. Full Stack Developer for LLM API Integration in B2B Marketplace
      +4. Full Stack Developer for LLM API Integration in B2B Marketplace

  [walk C] after another Down x21
[act] 02:46:30  fg='Upwork - Google Chrome'  key 'down'
[act] 02:46:30  fg='Upwork - Google Chrome'  key 'down'
[act] 02:46:30  fg='Upwork - Google Chrome'  key 'down'
[act] 02:46:30  fg='Upwork - Google Chrome'  key 'down'
[act] 02:46:31  fg='Upwork - Google Chrome'  key 'down'
[act] 02:46:31  fg='Upwork - Google Chrome'  key 'down'
[act] 02:46:31  fg='Upwork - Google Chrome'  key 'down'
[act] 02:46:32  fg='Upwork - Google Chrome'  key 'down'
[act] 02:46:33  fg='Upwork - Google Chrome'  key 'down'
[act] 02:46:34  fg='Upwork - Google Chrome'  key 'down'
[act] 02:46:34  fg='Upwork - Google Chrome'  key 'down'
[act] 02:46:34  fg='Upwork - Google Chrome'  key 'down'
[act] 02:46:36  fg='Upwork - Google Chrome'  key 'down'
[act] 02:46:36  fg='Upwork - Google Chrome'  key 'down'
[act] 02:46:36  fg='Upwork - Google Chrome'  key 'down'
[act] 02:46:37  fg='Upwork - Google Chrome'  key 'down'
[act] 02:46:37  fg='Upwork - Google Chrome'  key 'down'
[act] 02:46:37  fg='Upwork - Google Chrome'  key 'down'
[act] 02:46:37  fg='Upwork - Google Chrome'  key 'down'
[act] 02:46:38  fg='Upwork - Google Chrome'  key 'down'
[act] 02:46:38  fg='Upwork - Google Chrome'  key 'down'
    cards visible: 5, new since A+B: 0

  TOTAL UNIQUE CARDS at this zoom: 9
[act] 02:46:43  fg='Upwork - Google Chrome'  key 'ctrl+home'

  [panel-test] picked first card: 'AI Prompting & Automation Specialist (Claude · Gemini · Chat'
    bounds=(690, 363, 1034, 374)  mid_y=368  width=344px  height=11px
    clicking...
[act] 02:46:47  fg='Upwork - Google Chrome'  click name='AI Prompting & Automation Specialist (Cl' bounds=(690, 363, 1034, 374) target_xy=(867,369)
    capturing panel...
[panel] Copy visible at top; clicking now bounds=(1790, 469, 1816, 478)
[act] 02:46:54  fg='Upwork - Google Chrome'  click_xy (1803,473)
[panel] URL captured: https://www.upwork.com/jobs/~022051407361763230919
[panel] reached end of panel after 4 press(es); merged 399 elements; url=YES
    panel: 399 elements, url='https://www.upwork.com/jobs/~022051407361763230919'
[panel.parse] budget extraction trail: ["HOURLY@47 <- 'Hourly'", "MONEY@55 <- '$15.00'", "MONEY@56 <- '$20.00'", "HOURLY@57 <- 'Hourly'", "MONEY@118 <- '$9.29'", "FIXED@144 <- 'Fixed-price'", "MONEY@145 <- '$300.00'", "FIXED@158 <- 'Fixed-price'", "FIXED@170 <- 'Fixed-price'", "HOURLY@237 <- 'Hourly'", "MONEY@245 <- '$15.00'", "MONEY@246 <- '$20.00'", "HOURLY@247 <- 'Hourly'", "MONEY@290 <- '$9.29'", "FIXED@316 <- 'Fixed-price'", "MONEY@317 <- '$300.00'", "FIXED@330 <- 'Fixed-price'", "FIXED@342 <- 'Fixed-price'", "HOURLY@378 <- 'Hourly'", "HOURLY@382 <- 'Hourly'", "HOURLY@386 <- 'Hourly'", "HOURLY@390 <- 'Hourly'", "HOURLY@394 <- 'Hourly'"] -> kind='hourly'
    parsed: budget_kind=hourly  budget_min=15.0  country=United States
            description chars: 1349
            skills count: 5
[act] 02:47:08  fg='Upwork - Google Chrome'  key 'escape'

============================================================
  ZOOM LEVEL: 25%  (Ctrl+- x7)
============================================================
[act] 02:47:10  fg='Upwork - Google Chrome'  key 'ctrl+home'

  [walk A] top of feed
    cards visible: 8, 'Posted' anchors: 8, walk: 4.75s
      1. AI Prompting & Automation Specialist (Claude · Gemini · ChatGPT) with n8n experi
      2. Senior Node.js Engineer — On-Call Maintenance for AI Document Pipeline (Ongoing,
      3. AI Automations Developer Needed for Innovative Projects
      4. AI Engineer (Chat + Voice + Memory) for Monetized AI Companion Platform (Open AI
      5. AI Technical Architect / Advisor
      6. Full-Stack Engineer (Contract) — AI-Native Internal Tools & Products
      7. AI Voice Agent Using Retell AI
      8. Claude Code Power User — Production Bot Hardening (Python + Automation)

  [walk B] after Down x21
[act] 02:47:22  fg='Upwork - Google Chrome'  key 'down'
[act] 02:47:22  fg='Upwork - Google Chrome'  key 'down'
[act] 02:47:22  fg='Upwork - Google Chrome'  key 'down'
[act] 02:47:32  fg='Upwork - Google Chrome'  key 'down'
[act] 02:47:32  fg='Upwork - Google Chrome'  key 'down'
[act] 02:47:32  fg='Upwork - Google Chrome'  key 'down'
[act] 02:47:33  fg='Upwork - Google Chrome'  key 'down'
[act] 02:47:33  fg='Upwork - Google Chrome'  key 'down'
[act] 02:47:35  fg='Upwork - Google Chrome'  key 'down'
[act] 02:47:35  fg='Upwork - Google Chrome'  key 'down'
[act] 02:47:35  fg='Upwork - Google Chrome'  key 'down'
[act] 02:47:47  fg='Upwork - Google Chrome'  key 'down'
[act] 02:47:47  fg='Upwork - Google Chrome'  key 'down'
[act] 02:47:48  fg='Upwork - Google Chrome'  key 'down'
[act] 02:47:48  fg='Upwork - Google Chrome'  key 'down'
[act] 02:47:48  fg='Upwork - Google Chrome'  key 'down'
[act] 02:47:50  fg='Upwork - Google Chrome'  key 'down'
[act] 02:47:51  fg='Upwork - Google Chrome'  key 'down'
[act] 02:47:51  fg='Upwork - Google Chrome'  key 'down'
[act] 02:47:51  fg='Upwork - Google Chrome'  key 'down'
[act] 02:47:51  fg='Upwork - Google Chrome'  key 'down'
    cards visible: 7, new since A: 2
      +1. Full Stack Developer for LLM API Integration in B2B Marketplace
      +2. Full Stack Developer for LLM API Integration in B2B Marketplace

  [walk C] after another Down x21
[act] 02:47:56  fg='Upwork - Google Chrome'  key 'down'
[act] 02:47:58  fg='Upwork - Google Chrome'  key 'down'
[act] 02:47:58  fg='Upwork - Google Chrome'  key 'down'
[act] 02:48:00  fg='Upwork - Google Chrome'  key 'down'
[act] 02:48:00  fg='Upwork - Google Chrome'  key 'down'
[act] 02:48:01  fg='Upwork - Google Chrome'  key 'down'
[act] 02:48:01  fg='Upwork - Google Chrome'  key 'down'
[act] 02:48:01  fg='Upwork - Google Chrome'  key 'down'
[act] 02:48:01  fg='Upwork - Google Chrome'  key 'down'
[act] 02:48:02  fg='Upwork - Google Chrome'  key 'down'
[act] 02:48:04  fg='Upwork - Google Chrome'  key 'down'
[act] 02:48:04  fg='Upwork - Google Chrome'  key 'down'
[act] 02:48:05  fg='Upwork - Google Chrome'  key 'down'
[act] 02:48:05  fg='Upwork - Google Chrome'  key 'down'
[act] 02:48:05  fg='Upwork - Google Chrome'  key 'down'
[act] 02:48:06  fg='Upwork - Google Chrome'  key 'down'
[act] 02:48:06  fg='Upwork - Google Chrome'  key 'down'
[act] 02:48:06  fg='Upwork - Google Chrome'  key 'down'
[act] 02:48:06  fg='Upwork - Google Chrome'  key 'down'
[act] 02:48:07  fg='Upwork - Google Chrome'  key 'down'
[act] 02:48:07  fg='Upwork - Google Chrome'  key 'down'
    cards visible: 7, new since A+B: 0

  TOTAL UNIQUE CARDS at this zoom: 9
[act] 02:48:12  fg='Upwork - Google Chrome'  key 'ctrl+home'

  [panel-test] picked first card: 'AI Prompting & Automation Specialist (Claude · Gemini · Chat'
    bounds=(755, 299, 1013, 308)  mid_y=303  width=258px  height=9px
    clicking...
[act] 02:48:16  fg='Upwork - Google Chrome'  click name='AI Prompting & Automation Specialist (Cl' bounds=(755, 299, 1013, 308) target_xy=(871,303)
    capturing panel...
    panel: 0 elements, url=''
    parsed: budget_kind=None  budget_min=None  country=None
            description chars: 0
            skills count: 0
[act] 02:48:22  fg='Upwork - Google Chrome'  key 'escape'

=== Done. ===
Look at: cards-per-walk, total-unique, panel-walk success per zoom.
If 50% gets all 10 cards in 1 walk AND panel still works, big win.
If 33% works, we may not need PASS B/C at all.

(computer-use) PS D:\Personal\Projects\computer-use> 