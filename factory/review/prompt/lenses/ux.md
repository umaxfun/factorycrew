UI/UX. Judge what a USER experiences, not the code. Every new or changed user-facing string
read alone: does a first-time user understand it? Action labels are verb + object (no bare
OK/Submit/Continue); the label keeps its promise (trace the handler). Errors say what happened
and what to do next, in at most two sentences, no raw codes. Empty, loading, error, offline and
timeout states exist for every new async surface. Feedback for every action. Consistency with
sibling screens. Every element earns its place. Accessibility: keyboard reach, focus order,
labels and alt text, colour-only signals, contrast, motion. Narrow screens. Screenshots: when
you are given a BLIND READ, it is what a first-time user understood from the screenshots alone,
before anyone saw the diff or the intent. Reconcile it with the diff: a control the blind reader
misread, or could not tell the meaning of, is a candidate (the label does not keep its promise),
quoted from the BLIND READ. __UX_EVIDENCE__ NOT yours: backend logic, code style.
