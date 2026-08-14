# Rebuild Decision Checklist

Use this checklist during the frozen-project audit. The goal is to resolve only decisions that materially affect the clean backend rebuild.

## Must recover before implementation

- [ ] Exact Wedding Floral V10 package located by hash
- [ ] Exact template logical files/assets/fonts verified
- [ ] Asset/font licensing status noted
- [ ] Canonical guest-field keys confirmed
- [ ] Invitation-type allowed values confirmed from accepted legacy behavior
- [ ] Default companion maximum confirmed as 2
- [ ] Excel duplicate identity rule recovered or flagged for user decision
- [ ] Excel staged-row correction contract defined
- [ ] QR token generation/hash algorithm decision confirmed
- [ ] Card name-presentation policy recovered
- [ ] Attendance duplicate/idempotency contract recovered
- [ ] Companion admission/effective attendance semantics recovered
- [ ] Report metric definitions recovered

## Do not spend time recovering unless needed

- abandoned route counts
- old commit hashes except as evidence
- obsolete staging database state
- obsolete temporary test harnesses
- discarded template candidate versions
- experimental UI concepts
- old generated artifacts
- private real guest data

## User decision triggers

Stop and ask only when the frozen evidence is contradictory on a decision that blocks the current milestone.
