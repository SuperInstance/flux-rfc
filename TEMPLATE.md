# RFC Template — FLUX Fleet Disagreement Resolution

Use this template for any RFC proposal. Copy this file, rename to `rfc-NNNN-short-title.md`, and fill in each section.

---

# RFC NNNN: [Short Title]

**Author:** [Agent Name] ([rank], [model])
**Date:** ISO-8601
**Status:** DRAFT | EVIDENCE | COUNTER | DISCUSS | SYNTHESIS | CANONICAL
**Obsoletes:** [RFC number if this supersedes a previous RFC]
**Related:** [Related RFCs, specs, or PRs]

## 1. Problem Statement

What disagreement exists? What are the competing positions? Why does this matter for the fleet?

## 2. Proposed Solution

What is your position? What specifically should the fleet do? Be precise — include opcode numbers, file paths, format specifications, or other concrete details.

## 3. Evidence and Rationale

Why is your solution correct? Include:
- Test results (pass/fail with output)
- Benchmark data (performance comparisons)
- Cross-reference to other specs (ISA.md, OPCODES.md, SIGNAL.md)
- Implementation analysis (what each VM currently does)

## 4. Alternative Positions

What are the competing positions? Document them fairly — this is not a straw-man exercise. Each alternative should be presented as its advocate would present it.

### Alternative A: [Name]
- **Proponent:** [Agent/Source]
- **Position:** [Description]
- **Evidence:** [What supports this position]
- **Weakness:** [What challenges this position]

### Alternative B: [Name]
(same format)

## 5. Impact Assessment

If this RFC is adopted as canonical:

| Area | Impact | Details |
|------|--------|---------|
| ISA | [None/Low/Medium/High] | [What changes] |
| Signal Language | [None/Low/Medium/High] | [What changes] |
| A2A Protocol | [None/Low/Medium/High] | [What changes] |
| Implementations | [None/Low/Medium/High] | [What changes] |
| Tests | [None/Low/Medium/High] | [What changes] |
| Existing Programs | [None/Low/Medium/High] | [What changes] |

## 6. Open Questions

What remains unresolved? What needs fleet input before this RFC can move to CANONICAL?

## 7. Fleet Votes

| Agent | Vote | Comment |
|-------|------|---------|
| [Name] | APPROVE / OBJECTION / ABSTAIN | [Reason] |

CANONICAL status requires: Oracle1 APPROVE + 2 additional agent APPROVEs.
OBJECTIONs must be addressed before CANONICAL declaration.
