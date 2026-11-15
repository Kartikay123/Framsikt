"""Two people cannot both commit to the same refill: this is what stops double buying."""
import threading
import unittest

from snackbot import AlreadyCommitted, ConflictError, Person, RefillState as S
from tests.world import make_world


class CommitRaceTests(unittest.TestCase):
    def test_second_person_to_press_the_button_is_turned_away(self):
        w = make_world()
        refill = w.service.report_empty("OSL-1", "dag")                 # Anna is asked
        post = w.channel_post_with_button(refill)
        self.assertEqual(w.chat.click(post.id, "volunteer", "bjorn"), "committed:bjorn")
        self.assertIn("took the", w.chat.last_to("anna").text, "the nominee is told to stand down")

        result = w.chat.click(post.id, "volunteer", "cecilie")
        self.assertTrue(result.startswith("rejected:"))
        self.assertIn("Bjørn Dahl already has this one", w.chat.last_to("cecilie").text)
        with self.assertRaises(AlreadyCommitted) as ctx:
            w.service.commit(refill.id, "dag")
        self.assertEqual(ctx.exception.buyer_id, "bjorn")
        self.assertEqual(w.refill(refill.id).buyer_id, "bjorn")

    def test_concurrent_commits_exactly_one_wins(self):
        w = make_world()
        for i in range(24):
            w.repo.add_person(Person(f"v{i:02d}", f"Volunteer {i}", "OSL", "NOK"))
        refill = w.service.report_empty("OSL-1", "dag")
        contenders = [p.id for p in w.repo.people.values() if p.volunteer and p.home_office == "OSL"]
        barrier = threading.Barrier(len(contenders))
        results: dict[str, str] = {}

        def try_commit(user_id: str) -> None:
            barrier.wait()
            try:
                w.service.commit(refill.id, user_id)
                results[user_id] = "won"
            except AlreadyCommitted as e:
                results[user_id] = f"lost to {e.buyer_id}"

        threads = [threading.Thread(target=try_commit, args=(u,)) for u in contenders]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        winners = [u for u, r in results.items() if r == "won"]
        self.assertEqual(len(winners), 1, results)
        self.assertEqual(set(results.values()) - {"won"}, {f"lost to {winners[0]}"})
        final = w.refill(refill.id)
        self.assertEqual((final.state, final.buyer_id), (S.COMMITTED, winners[0]))
        self.assertEqual(sum(1 for e in final.history if e.type == "committed"), 1)
        self.assertEqual(sum(1 for m in w.chat.in_channel("#snacks-oslo") if "is buying" in m.text), 1)

    def test_two_simultaneous_empty_reports_open_one_refill(self):
        w = make_world()
        reporters = ["anna", "bjorn", "cecilie", "dag", "adm-oslo"]
        barrier = threading.Barrier(len(reporters))

        def report(user_id: str) -> None:
            barrier.wait()
            w.service.report_empty("OSL-1", user_id)

        threads = [threading.Thread(target=report, args=(u,)) for u in reporters]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(len(w.repo.refills()), 1)
        self.assertEqual(len([m for m in w.chat.in_channel("#snacks-oslo") if "is empty" in m.text]), 1)

    def test_the_store_rejects_stale_writes(self):
        w = make_world()
        refill = w.service.report_empty("OSL-1", "dag")
        a = w.repo.get_refill(refill.id)
        b = w.repo.get_refill(refill.id)
        a.note = "first writer"
        w.repo.save_refill(a)
        b.note = "second writer, working from a stale copy"
        with self.assertRaises(ConflictError):
            w.repo.save_refill(b)
        self.assertEqual(w.refill(refill.id).note, "first writer")


if __name__ == "__main__":
    unittest.main()
