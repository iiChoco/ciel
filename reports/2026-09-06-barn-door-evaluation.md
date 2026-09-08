# Barn Door needs evidence from the room

2026-09-06. The user reports that the previous attempt rejected their voice,
accepted other voices, or had additional failures. This inspection establishes
evaluation gaps; it does not establish the cause of those particular failures.
No enrolled profile, rejected clip, runtime config, or production log was read.
Existing unrelated source edits were preserved.

1. **Calibration does not evaluate the full runtime acceptance policy.**
   `scripts/enroll_voice.py:382` chooses a stored threshold from impostor
   best-match scores and the enrollment takes' leave-one-out similarities.
   `src/ciel/audio/speaker.py:336` lowers that threshold for short utterances
   or a recent accepted utterance. The companion script reproduces the
   distinction with synthetic unit vectors: at a stored threshold of 0.70,
   the same score of 0.60 is rejected for three seconds of audio and accepted
   for 600 milliseconds (the effective threshold is 0.58). This is intentional
   runtime leniency, but a gap above calibration negatives does not establish
   rejection under all runtime conditions. Proposed correction: evaluate and
   calibrate the whole gate on held-out utterance sequences, including duration
   and conversation context, and report both false accepts and false rejects.
   Do not simply raise or lower the threshold without measuring both.

2. **The calibration corpus does not establish real-room discrimination.**
   `scripts/enroll_voice.py:273` synthesizes macOS say voices directly to WAV
   and embeds those files. It does not pass the negative examples through
   the physical room and microphone. Positive estimates use enrollment takes
   against other enrollment takes (`scripts/enroll_voice.py:323`), rather
   than an independent later recording session. Neither synthetic impostor
   separation nor fake-encoder probes measures the user's normal speech,
   another person in the room, television playback, speaker echo, or overlap.
   Proposed correction: use explicitly collected, labeled evaluation recordings
   from the actual microphone, across sessions and conditions. Keep evaluation
   recordings separate from enrollment, and keep those recordings local and
   outside reports and Git. Document coverage and missing conditions.

Recommended next milestone: evaluate in a diagnostic mode that reports what
Barn Door would accept or reject without allowing its verdicts to block the
user. Track capture quality, captured speech duration, similarity, effective
threshold, and decision reason. Separate wake/capture failures from identity
failures. Only make it the active gate after the agreed real-use cases pass.
No model change, new dependency, profile change, or service restart is proposed
as an immediate substitute for that evidence.

Verification: `uv run --no-sync python reports/2026-09-06-barn-door-evaluation-repro.py`
passed using temporary state and a fake encoder. These values are a policy
fixture, not a measured score distribution or an estimate of recognition
accuracy. No live acoustic test or complete regression suite was run. No
application behavior was changed.
