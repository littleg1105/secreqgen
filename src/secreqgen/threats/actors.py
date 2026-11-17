# ruff: noqa: E501
"""The closed list of threat actors (List A of the registered threat step of the thesis)."""

# The threat actor of a row: one value of the closed list of the thesis (List A of the registered threat step).
# It is the model's choice from the threat sentence. It has no source in the documentation.
ACTORS = {
    "external_individual": "external individual (one person outside the organisation)",
    "external_group": "external group (persons outside the organisation who work together)",
    "external_organization": "external organisation (for example a partner, a supplier or a competitor)",
    "nation_state": "nation state (a government or an agency of a state)",
    "trusted_insider": "trusted insider (a person with ordinary access)",
    "privileged_insider": "privileged insider (a person with administrator or operator rights)",
    "accidental_actor": "accidental actor (a user or an administrator who causes harm by mistake)",
    "other": "other",
}
