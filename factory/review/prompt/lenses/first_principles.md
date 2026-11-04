FIRST PRINCIPLES / DESIGN. State the problem in one sentence; if no real harm or user is
nameable, that is the candidate. Does the change actually solve it end to end? Root cause or
symptom? Name the one or two strongest simpler alternatives. Architectural fit: is the logic
in the area the overview and area docs assign it to? Contract and data evolution: one-way
doors (persisted schema, public interface, defaults) with no migration or compatibility story.
Failure modes: disconnects, concurrency, restart midway. Product shape: does the change alter
what the product does (a new capability, a removed behaviour, a changed contract between
areas)? Then find the decision that covers it: the feature design (also when the merge request
ships the feature branch it was approved into: then the design reaches the target with this
merge request, and anything beyond it is still a candidate), a design in the designs folder on
the target branch, a decision in an area doc, or a closed issue written by a person
in `humans` whose tier is below the first tier that needs a design (`below it: yes` under
Closed issues): such an issue is the decision for exactly what it asks, and only that; a
borderline decision below that tier (Borderline decisions) is one for exactly its case. None
found is a candidate with anchor `product-shape`: the change may have been sorted into too
low a tier. Tier rule (each closed issue's `effect` under Closed issues): a tier says what the
change does to the product. For `effect fix` (T1) the triager cited the documented line the
code contradicts (`triage citation`; the quote is in issues.md): the change may do only what
that cited spec line requires; any behaviour it changes beyond that line, a citation `none`,
or a quote that is not on that line of the target, is a `product-shape` candidate (the issue is
not a fix). For `effect addition` (T2): any change to existing behaviour (a default, an
output, a message, a rule) is a `product-shape` candidate. For `effect change` or `effect
concept` (T3, T4): anything the approved design does not cover is one; `concept` needs the
full design (why, alternatives, how it fits, the new area and its doc). A new area or a new
term in the overview below `effect concept` is one too. Inventory each thing the merge request ships: does each deserve to exist, does it
duplicate an existing helper, does it have a current consumer? EVERY suggestion is a
SUBTRACTION (delete, shrink, defer, reuse). Never propose adding a layer, knob or abstraction.
Every candidate ends with a `Clears when:` clause.
