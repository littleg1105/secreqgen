# ruff: noqa: E501
"""The closed lists of the label step: threat actors (List A of the registered threat step) and STRIDE categories."""

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

# The STRIDE category of a row: the model's choice from the threat sentence. "none" means: no security threat.
STRIDE = {
    "S": "S, spoofing (a false identity)",
    "T": "T, tampering (data or code is changed with no permission)",
    "R": "R, repudiation (an action can be denied, because there is no record)",
    "I": "I, information disclosure (data is shown to somebody who must not see it)",
    "D": "D, denial of service (the service stops or is too slow)",
    "E": "E, elevation of privilege (rights or access that somebody must not have)",
}
