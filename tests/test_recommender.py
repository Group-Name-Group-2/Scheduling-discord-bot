"""recommender.py: picking a time and a game for a group."""

from datetime import datetime

import pytest

import database
from recommender import (
    busy_slots_from_events,
    hours_needed,
    pick_alternatives,
    rank_games,
    rank_time_slots,
    recommend_event,
)
from tests.conftest import ALICE, BOB, CAROL

# A fixed "now" so results never depend on when the tests run.
NOW = datetime(2026, 11, 2, 12, 0)   # Monday
SAT = "2026-11-07"
SUN = "2026-11-08"
NEXT_SAT = "2026-11-14"


def free(*slots):
    return set(slots)


# ---------------------------------------------------------------------
# hours_needed
# ---------------------------------------------------------------------

@pytest.mark.parametrize("minutes, hours", [
    (0, 1), (30, 1), (60, 1), (61, 2), (90, 2), (120, 2), (180, 3), (240, 4),
])
def test_hours_needed(minutes, hours):
    assert hours_needed(minutes) == hours


# ---------------------------------------------------------------------
# busy_slots_from_events
# ---------------------------------------------------------------------

class TestBusySlots:
    def test_marks_every_hour_touched(self):
        events = [{"event_date": SAT, "event_time": "19:30",
                   "duration_minutes": 90, "members": {ALICE, BOB}}]
        busy = busy_slots_from_events(events, [ALICE])
        # 19:30-21:00 touches the 19:00 and 20:00 blocks.
        assert busy == {ALICE: {(SAT, 19), (SAT, 20)}}

    def test_ignores_people_not_asked_about(self):
        events = [{"event_date": SAT, "event_time": "19:00",
                   "duration_minutes": 60, "members": {BOB}}]
        assert busy_slots_from_events(events, [ALICE]) == {}

    def test_event_past_midnight_spills_into_next_day(self):
        events = [{"event_date": SAT, "event_time": "23:00",
                   "duration_minutes": 120, "members": {ALICE}}]
        assert busy_slots_from_events(events, [ALICE])[ALICE] == {(SAT, 23), (SUN, 0)}


# ---------------------------------------------------------------------
# rank_time_slots
# ---------------------------------------------------------------------

class TestRankTimeSlots:
    def test_most_attendees_wins(self):
        availability = {
            ALICE: free((SAT, 14), (SAT, 20)),
            BOB: free((SAT, 20)),
        }
        slots = rank_time_slots([ALICE, BOB], availability, {}, 60, NOW)
        assert (slots[0]["date"], slots[0]["hour"]) == (SAT, 20)
        assert slots[0]["attendees"] == [ALICE, BOB]

    def test_sooner_date_beats_later_with_same_attendees(self):
        availability = {ALICE: free((SAT, 19), (NEXT_SAT, 19))}
        slots = rank_time_slots([ALICE], availability, {}, 60, NOW)
        assert slots[0]["date"] == SAT

    def test_closer_to_19_breaks_ties(self):
        availability = {ALICE: free((SAT, 10), (SAT, 18))}
        slots = rank_time_slots([ALICE], availability, {}, 60, NOW)
        assert slots[0]["hour"] == 18

    def test_buffer_beats_preferred_hour(self):
        # 19:00 alone vs 14:00 with free hours either side.
        availability = {ALICE: free((SAT, 13), (SAT, 14), (SAT, 15), (SAT, 19))}
        slots = rank_time_slots([ALICE], availability, {}, 60, NOW)
        assert slots[0]["hour"] == 14

    def test_must_be_free_for_whole_event(self):
        availability = {ALICE: free((SAT, 19))}
        assert rank_time_slots([ALICE], availability, {}, 120, NOW) == []

    def test_busy_slots_excluded(self):
        availability = {ALICE: free((SAT, 19))}
        busy = {ALICE: {(SAT, 19)}}
        assert rank_time_slots([ALICE], availability, busy, 60, NOW) == []

    def test_past_times_skipped(self):
        now = datetime(2026, 11, 7, 19, 0)
        availability = {ALICE: free((SAT, 18), (SAT, 19), (SAT, 20))}
        hours = [s["hour"] for s in rank_time_slots([ALICE], availability, {}, 60, now)]
        assert hours == [20]

    def test_people_without_availability_neither_help_nor_hurt(self):
        availability = {ALICE: free((SAT, 19))}
        slots = rank_time_slots([ALICE, BOB], availability, {}, 60, NOW)
        assert slots[0]["attendees"] == [ALICE]

    def test_event_cannot_run_past_midnight(self):
        availability = {ALICE: free((SAT, 23))}
        assert rank_time_slots([ALICE], availability, {}, 120, NOW) == []


# ---------------------------------------------------------------------
# pick_alternatives
# ---------------------------------------------------------------------

class TestPickAlternatives:
    def slot(self, day, hour):
        return {"date": day, "hour": hour, "attendees": [ALICE]}

    def test_skips_overlapping_times(self):
        slots = [self.slot(SAT, 19), self.slot(SAT, 20), self.slot(SAT, 21), self.slot(SUN, 19)]
        alternatives = pick_alternatives(slots, needed=2)
        assert [(s["date"], s["hour"]) for s in alternatives] == [(SAT, 21), (SUN, 19)]

    def test_at_most_three(self):
        slots = [self.slot(SAT, h) for h in range(8, 24)]
        assert len(pick_alternatives(slots, needed=1)) == 3

    def test_none_when_only_one_slot(self):
        assert pick_alternatives([self.slot(SAT, 19)], needed=1) == []


# ---------------------------------------------------------------------
# rank_games
# ---------------------------------------------------------------------

class TestRankGames:
    def test_owned_by_more_attendees_first(self):
        ownership = {
            1: {"name": "Solo", "playtime": {ALICE: 5000}},
            2: {"name": "Party", "playtime": {ALICE: 10, BOB: 10}},
        }
        games = rank_games(ownership, [ALICE, BOB], [ALICE, BOB])
        assert [g["name"] for g in games] == ["Party", "Solo"]

    def test_invited_owners_break_ties(self):
        ownership = {
            1: {"name": "A", "playtime": {ALICE: 0}},
            2: {"name": "B", "playtime": {ALICE: 0, CAROL: 0}},
        }
        games = rank_games(ownership, [ALICE], [ALICE, CAROL])
        assert games[0]["name"] == "B"
        assert games[0]["owned_by_invited"] == 2

    def test_played_beats_unplayed(self):
        ownership = {
            1: {"name": "Unplayed", "playtime": {ALICE: 0, BOB: 0}},
            2: {"name": "Played", "playtime": {ALICE: 60, BOB: 0}},
        }
        assert rank_games(ownership, [ALICE, BOB], [ALICE, BOB])[0]["name"] == "Played"

    def test_name_is_last_tie_break(self):
        ownership = {
            1: {"name": "zeta", "playtime": {ALICE: 0}},
            2: {"name": "Alpha", "playtime": {ALICE: 0}},
        }
        assert [g["name"] for g in rank_games(ownership, [ALICE], [ALICE])] == ["Alpha", "zeta"]

    def test_required_owner_filters(self):
        ownership = {
            1: {"name": "Bobs", "playtime": {BOB: 10}},
            2: {"name": "Shared", "playtime": {ALICE: 1, BOB: 10}},
        }
        games = rank_games(ownership, [ALICE, BOB], [ALICE, BOB], required_owner=ALICE)
        assert [g["name"] for g in games] == ["Shared"]

    def test_games_no_attendee_owns_dropped(self):
        ownership = {1: {"name": "X", "playtime": {CAROL: 100}}}
        assert rank_games(ownership, [ALICE], [ALICE, CAROL]) == []

    def test_hours_and_counts(self):
        ownership = {1: {"name": "X", "playtime": {ALICE: 90, BOB: 30, CAROL: 600}}}
        game = rank_games(ownership, [ALICE, BOB], [ALICE, BOB, CAROL])[0]
        assert game == {
            "appid": 1, "name": "X", "owned_by": 2, "owned_by_invited": 3,
            "played_by": 2, "hours": 2.0,
        }

    def test_no_internal_score_leaks(self):
        ownership = {1: {"name": "X", "playtime": {ALICE: 1}}}
        assert "_score" not in rank_games(ownership, [ALICE], [ALICE])[0]


# ---------------------------------------------------------------------
# recommend_event (with the database)
# ---------------------------------------------------------------------

class TestRecommendEvent:
    def test_no_people(self):
        assert recommend_event([], now=NOW) == {"ok": False, "reason": "no_people"}

    def test_no_availability(self, make_user):
        make_user(ALICE)
        assert recommend_event([ALICE], now=NOW)["reason"] == "no_availability"

    def test_no_slots_when_everyone_busy(self, make_user):
        make_user(ALICE, games=[(10, "Game")])
        database.replace_availability(ALICE, [SAT], [(SAT, 19)])
        database.save_game_night_event(ALICE, 10, SAT, "19:00", 60, [])
        assert recommend_event([ALICE], duration_minutes=60, now=NOW)["reason"] == "no_slots"

    def test_full_recommendation(self, make_user):
        make_user(ALICE, games=[(10, "Shared", 120), (11, "Alice only", 9000)])
        make_user(BOB, games=[(10, "Shared", 60)])
        make_user(CAROL)

        database.replace_availability(ALICE, [SAT], [(SAT, 19), (SAT, 20)])
        database.replace_availability(BOB, [SAT], [(SAT, 19), (SAT, 20)])

        result = recommend_event(
            [ALICE, BOB, CAROL, ALICE],   # duplicate is ignored
            duration_minutes=120,
            required_game_owner=ALICE,
            now=NOW,
        )

        assert result["ok"]
        assert (result["date"], result["time"]) == (SAT, "19:00")
        assert result["attendees"] == [ALICE, BOB]
        assert result["no_availability"] == [CAROL]
        assert result["unavailable"] == []
        assert result["game"]["name"] == "Shared"
        assert [g["name"] for g in result["alternative_games"]] == ["Alice only"]

    def test_unavailable_people_listed(self, make_user):
        make_user(ALICE)
        make_user(BOB)
        database.replace_availability(ALICE, [SAT], [(SAT, 19)])
        database.replace_availability(BOB, [SUN], [(SUN, 10)])

        result = recommend_event([ALICE, BOB], duration_minutes=60, now=NOW)
        # Tie on attendee count (1 each): the sooner date wins.
        assert result["date"] == SAT
        assert result["unavailable"] == [BOB]
        assert result["game"] is None

    def test_existing_event_blocks_time(self, make_user):
        make_user(ALICE, games=[(10, "Game")])
        database.replace_availability(ALICE, [SAT], [(SAT, 19), (SAT, 21)])
        database.save_game_night_event(ALICE, 10, SAT, "19:00", 60, [])

        result = recommend_event([ALICE], duration_minutes=60, now=NOW)
        assert result["time"] == "21:00"
