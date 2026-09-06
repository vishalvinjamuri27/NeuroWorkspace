# Alpha user outreach

No external-user milestone is claimed in this document. It is a staged,
permission-based recruiting plan. Do not scrape addresses, automate unsolicited
messages, or post repeatedly to communities.

## Cohort 1: five observed usability testers

Recruit one or two people from each of: EEG PhD/neuroscience graduate students,
MNE or EEGLAB users, BCI club/OpenBCI builders, and neuroengineering lab or
neurotech engineers. Start with warm introductions, university lab contacts,
local BCI clubs, and maintainers/community spaces that explicitly allow project
feedback requests.

Why they may care: fast browser review, shareable workspace organization, visible
provenance, and less setup for common inspection/QC/annotation tasks.

Test one realistic recording from their current workflow in a live 20–30 minute
session. The signal is not “liked it”; it is opening permitted own data, completing
the core task with little help, and naming a concrete reason to return.

## Cohort 2: ten own-data testers

After the first five sessions have no shared blocker, invite ten external EEG users
to try one supported, deidentified recording asynchronously. Recruit through
individual follow-ups, MNE/EEGLAB/OpenBCI community channels that welcome tool
announcements, neuroscience graduate groups, BCI clubs, and neuroengineering lab
contacts. Lead with the public sample so nobody must trust the upload path first.

Test format reality, upload error quality, long-recording navigation, return/reopen,
and export. Retention signal: a second session or second recording without a
personal reminder.

## Cohort 3: twenty-five-person alpha

Invite a balanced cohort only after support capacity, backups, monitoring, privacy
notice, and deployment restart checks exist. Include different supported formats,
recording lengths, operating systems, and current tools. Publish one neutral
open-source announcement with the sample link, scope, data warning, known
limitations, and feedback route.

Test repeated workflows rather than a feature wishlist. Retention signal: multiple
people reopening recordings and at least one recurring problem cluster that
justifies a roadmap decision.

## Cohort 4: one hundred-person distribution

Expand through a tagged public alpha release, project documentation, permitted
community posts, conference/lab networks, and earned sharing by earlier users.
Do this only when operator response time, storage quotas, incident handling, and
cost visibility are adequate. Retention signal: return behavior outside founder-led
sessions, unsolicited issues, and real team workflow use.

## Segment map

| Segment | Why they may care | Where to find them respectfully | Workflow to test | Retention evidence |
|---|---|---|---|---|
| EEG PhD/graduate students | faster inspection and review notes | warm lab introductions, graduate groups | EDF/BrainVision/EEGLAB review and export | reopens during analysis |
| MNE users | browser review around Python pipelines | MNE forum/community channels that permit showcases | FIF/EDF inspect, QC, provenance export | uses before/after Python |
| EEGLAB users | simpler sharing and organization | EEGLAB list/forum where allowed, local labs | EEGLAB events, annotation, channel review | loads a second dataset |
| OpenBCI/BCI clubs | quick visual check without notebooks | local clubs and official community channels | CSV/BDF import and artifact marking | shares demo or returns |
| Neuroengineering labs | consistent review handoff | direct opt-in lab contacts | project organization and repeat review | second member or real session |
| Neurotech engineers/scientists | reproducibility and browser access | warm professional contacts, public OSS channels | long recording, errors, export | integrates into a real task |

## Product milestones

- **M0:** creator can reliably use the product.
- **M1:** five external users open their own EEG recordings.
- **M2:** ten external users successfully ingest data.
- **M3:** three users return without being personally prompted.
- **M4:** at least one user asks for a feature because they want to continue.
- **M5:** at least one lab/team uses the product during real work.
- **M6:** first unsolicited GitHub issue.
- **M7:** first external code contribution.
- **M8:** first organization expresses willingness to pay for a meaningful
  team/commercial capability.

At release preparation, M1–M8 are **not achieved**. M0 is a creator/internal
validation milestone only and must not be described as external adoption.

## First-five operating cadence

Book five individual sessions across two weeks. After sessions 1–2, fix only
release blockers. After sessions 3–5, cluster observations and choose one of:
continue unchanged, fix a repeated severe problem, narrow the audience, or stop.
The founder owns invitations, facilitation, response to feedback, and the decision
log. Engineering does not resume speculative expansion between sessions.
