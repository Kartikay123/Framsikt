"""A refill nobody completes is handed to someone else."""
import unittest
from datetime import datetime, timezone

from snackbot import RefillState as S
from tests.world import make_world


class HandoverTests(unittest.TestCase):
    def test_an_unanswered_nomination_passes_to_the_next_person(self):
        w = make_world()
        refill = w.service.report_empty("OSL-1", "dag")
        self.assertEqual(refill.nominee_id, "anna")
        w.clock.advance(hours=1, minutes=59)
        w.service.tick()
        self.assertEqual(w.refill(refill.id).nominee_id, "anna", "still within the two office hours")
        w.clock.advance(minutes=2)
        log = w.service.tick()
        refill = w.refill(refill.id)
        self.assertEqual((refill.state, refill.nominee_id, refill.passed), (S.NOMINATED, "bjorn", ["anna"]))
        self.assertIn("asked someone else", w.chat.last_to("anna").text)
        self.assertTrue(any("anna did not answer" in line for line in log), log)

    def test_the_answer_window_counts_office_hours_not_weekend_hours(self):
        friday_five_pm_oslo = datetime(2026, 11, 6, 16, 0, tzinfo=timezone.utc)
        w = make_world(start=friday_five_pm_oslo)
        refill = w.service.report_empty("OSL-1", "dag")
        self.assertEqual(w.refill(refill.id).nomination_expires_at,
                         datetime(2026, 11, 9, 8, 0, tzinfo=timezone.utc))      # Monday 09:00 CET
        w.clock.set(datetime(2026, 11, 7, 12, 0, tzinfo=timezone.utc))           # Saturday noon
        w.service.tick()
        self.assertEqual(w.refill(refill.id).nominee_id, "anna")
        w.clock.set(datetime(2026, 11, 9, 8, 1, tzinfo=timezone.utc))
        w.service.tick()
        self.assertEqual(w.refill(refill.id).nominee_id, "bjorn")

    def test_a_missed_purchase_deadline_hands_the_refill_to_someone_else(self):
        w = make_world()
        refill = w.accept(w.service.report_empty("OSL-1", "dag"))              # Anna, due Tue 18:00 CET
        w.clock.set(datetime(2026, 11, 3, 16, 59, tzinfo=timezone.utc))
        w.service.tick()
        self.assertEqual(w.refill(refill.id).buyer_id, "anna")
        w.clock.set(datetime(2026, 11, 3, 17, 1, tzinfo=timezone.utc))
        w.service.tick()
        refill = w.refill(refill.id)
        self.assertEqual((refill.state, refill.buyer_id, refill.nominee_id, refill.passed),
                         (S.NOMINATED, None, "bjorn", ["anna"]))
        self.assertIn("didn't get to", w.chat.in_channel("#snacks-oslo")[-1].text)
        self.assertIn("handed it to someone else", w.chat.last_to("anna").text)

    def test_declining_passes_it_on_immediately(self):
        w = make_world()
        refill = w.service.report_empty("OSL-1", "dag")
        ask = w.ask_for(refill)
        w.chat.click(ask.id, "decline", "anna")
        refill = w.refill(refill.id)
        self.assertEqual((refill.nominee_id, refill.passed), ("bjorn", ["anna"]))

    def test_handing_back_a_commitment_passes_it_on(self):
        w = make_world()
        refill = w.accept(w.service.report_empty("OSL-1", "dag"))
        hand_back = next(a for a in w.chat.last_to("anna").actions if a.id == "hand_back")
        w.chat.click(w.chat.last_to("anna").id, hand_back.id, "anna")
        refill = w.refill(refill.id)
        self.assertEqual((refill.state, refill.nominee_id, refill.passed), (S.NOMINATED, "bjorn", ["anna"]))
        self.assertIn("can't do", w.chat.in_channel("#snacks-oslo")[-1].text)

    def test_people_who_passed_are_not_asked_again_for_the_same_refill(self):
        w = make_world()
        refill = w.service.report_empty("OSL-1", "dag")                        # anna
        w.clock.advance(hours=3)
        w.service.tick()                                                        # anna timed out -> bjorn
        w.service.decline(refill.id, "bjorn")                                   # -> cecilie
        w.accept(refill)
        w.service.hand_back(refill.id, "cecilie")                               # -> dag, never back to anna
        refill = w.refill(refill.id)
        self.assertEqual(refill.nominee_id, "dag")
        self.assertEqual(refill.passed, ["anna", "bjorn", "cecilie"])

    def test_when_everyone_has_passed_the_office_is_alerted_once_and_anyone_can_still_volunteer(self):
        w = make_world()
        refill = w.service.report_empty("OSL-1", "dag")
        for who in ["anna", "bjorn", "cecilie", "dag"]:
            self.assertEqual(w.refill(refill.id).nominee_id, who)
            w.service.decline(refill.id, who)
        refill = w.refill(refill.id)
        self.assertEqual((refill.state, refill.nominee_id), (S.REPORTED, None))

        def alerts():
            return [m for m in w.chat.in_channel("#snacks-oslo") if "Nobody" in m.text]

        self.assertEqual(len(alerts()), 1)
        self.assertEqual(len([m for m in w.chat.to_user("adm-oslo") if "Nobody" in m.text]), 1)
        w.service.tick()
        w.service.tick()
        self.assertEqual(len(alerts()), 1, "the scheduler does not nag")

        post = w.channel_post_with_button(refill)
        self.assertEqual(w.chat.click(post.id, "volunteer", "dag"), "committed:dag")
        self.assertEqual(w.refill(refill.id).state, S.COMMITTED)


if __name__ == "__main__":
    unittest.main()
