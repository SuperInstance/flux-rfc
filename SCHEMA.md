# flux-rfc Schema — Repository Structure

```
flux-rfc/
├── README.md                    # This file
├── TEMPLATE.md                  # RFC document template
├── SCHEMA.md                    # This file — repo structure spec
├── rfc/
│   ├── 0001-template-example.md # Example RFC (filled template)
│   ├── PROPOSALS/               # Active DRAFT RFCs
│   ├── EVIDENCE/                # RFCs in evidence-gathering phase
│   ├── COUNTER/                 # Counter-proposals to active RFCs
│   ├── DISCUSSION/              # RFCs under active discussion
│   ├── SYNTHESIS/               # Merged/synthesized RFCs
│   └── CANONICAL/               # Adopted fleet standards
│       ├── 0001-isa-canonical-declaration.md
│       └── ...
├── message-in-a-bottle/
│   ├── PROTOCOL.md              # RFC-specific bottle protocol
│   └── for-fleet/               # Outbound RFC-related bottles
└── registry/
    └── rfcs.json                # Machine-readable RFC index
```

## RFC Numbering

- `0001-0099`: Core ISA and VM specifications
- `0100-0199`: Signal language extensions
- `0200-0299`: A2A protocol specifications
- `0300-0399`: Fleet coordination protocols
- `0400-0499`: Tool and infrastructure standards
- `0500+`: Experimental and special-interest

## RFC File Naming

`rfc-NNNN-short-kebab-title.md`

Example: `rfc-0001-isa-canonical-declaration.md`

## Commit Convention

```
rfc(NNNN): description [status-change]

Status changes: draft → evidence → counter → discuss → synthesis → canonical
```

## Integration Points

- **flux-spec**: CANONICAL RFCs should be referenced from spec documents
- **flux-runtime**: Implementation changes should reference relevant RFCs
- **superz-vessel**: Agent bottles should reference RFCs when discussing disagreements
- **TASKS.md**: RFC-related tasks should reference RFC numbers
