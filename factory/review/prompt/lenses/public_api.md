PUBLIC SURFACE. Judge what a CONSUMER of this repository experiences: a caller of the library,
a user of the CLI, a client of the API, an operator of its configuration. Inventory every
exported name, command, flag, option, config key, endpoint, message, output format and exit
code the diff adds, changes or removes. A removed or renamed one with an existing consumer and
no deprecation path is a candidate; so is a changed default, a changed output a script could
parse, or a new error that replaces a working result. New surface: is it named consistently
with its siblings, documented where its siblings are, and would a consumer find it? Check the
"Talks to / Used by" sections of the touched areas' docs for the consumers. NOT yours:
internals no consumer can reach, style.
