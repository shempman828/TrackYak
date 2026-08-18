"""Tests for bug #301: some credit roles weren't parsing from MusicBrainz on
album import. Root cause: _relation_role_name assumed attribute-list[0] was
always the instrument/vocal name, but MB's own link-phrase template for
these relations is "{additional} {guest} {solo} {instrument}" -- a
qualifier word can sit before the real value. Confirmed against the live
API: Eagles' "Hotel California" credits Bill Armstrong's trumpet as
attribute-list ["additional", "trumpet"], which the old code reported as
role "Additional", silently dropping "trumpet".

Also covers two relation types that were missing from the credit-type
whitelist entirely (confirmed live, dropped with no trace): "performing
orchestra" (Judy Garland's "Over the Rainbow") and "sound" (Queen's
"Bohemian Rhapsody").
"""

from src.musicbrainz import musicbrainz_release as mc


def _rel(type_, attributes=None):
    return {"type": type_, "attribute-list": attributes or []}


class TestPerformerRoleName:
    def test_plain_instrument_no_qualifier(self):
        assert mc._relation_role_name(_rel("instrument", ["trumpet"])) == "Trumpet"

    def test_qualifier_before_instrument_still_reports_instrument(self):
        # The real-world case: qualifier ("additional") sits at index 0,
        # ahead of the actual instrument value.
        rel = _rel("instrument", ["additional", "trumpet"])
        assert mc._relation_role_name(rel) == "Additional Trumpet"

    def test_guest_qualifier(self):
        rel = _rel("instrument", ["guest", "guitar"])
        assert mc._relation_role_name(rel) == "Guest Guitar"

    def test_multiple_instrument_values_in_one_relation(self):
        rel = _rel("instrument", ["piano", "organ"])
        assert mc._relation_role_name(rel) == "Piano & Organ"

    def test_qualifier_with_no_instrument_value_falls_back_to_qualifier(self):
        rel = _rel("instrument", ["additional"])
        assert mc._relation_role_name(rel) == "Additional"

    def test_vocal_relation_unaffected(self):
        assert mc._relation_role_name(_rel("vocal", ["lead vocals"])) == "Lead Vocals"

    def test_no_attributes_falls_back_to_type_name(self):
        assert mc._relation_role_name(_rel("performing orchestra")) == "Performing Orchestra"


class TestProductionRoleNameUnaffected:
    def test_modifier_still_prefixes_type(self):
        assert mc._relation_role_name(_rel("engineer", ["assistant"])) == "Assistant Engineer"

    def test_sound_with_additional_modifier(self):
        assert mc._relation_role_name(_rel("sound", ["additional"])) == "Additional Sound Engineer"

    def test_no_modifier_falls_back_to_type_name(self):
        assert mc._relation_role_name(_rel("producer")) == "Producer"

    def test_sound_with_no_modifier_reports_sound_engineer(self):
        # Bug #326: "sound" is the one production relation type whose own
        # name ("sound") isn't the full credited role -- MB's link phrase is
        # the compound "sound engineer", unlike "mastering"/"mix"/etc. which
        # are single words. Naive rel_type.title() silently dropped
        # "Engineer" from every plain sound-engineer credit.
        assert mc._relation_role_name(_rel("sound")) == "Sound Engineer"

    def test_mix_with_no_modifier_reports_mixer(self):
        # Same root cause as #326: MB's "mix" relation type credits as
        # "Mixer", not "Mix" -- confirmed live on Nirvana's Nevermind, where
        # Andy Wallace's mix relation has an empty attribute-list. Naive
        # rel_type.title() produced "Mix", not a real credit noun.
        assert mc._relation_role_name(_rel("mix")) == "Mixer"

    def test_mix_with_modifier_prefixes_mixer_not_mix(self):
        assert mc._relation_role_name(_rel("mix", ["assistant"])) == "Assistant Mixer"

    def test_recording_with_no_modifier_reports_recording_engineer(self):
        # "recording" was entirely missing from the credit-type whitelist
        # (silently dropped, not mislabeled) until this was added alongside
        # the sound/mix display-name fixes. Confirmed live on Nirvana's
        # Nevermind ("Something in the Way" credits James Johnson, Jeff
        # Sheehan, and Butch Vig via a plain "recording" relation).
        assert mc._relation_role_name(_rel("recording")) == "Recording Engineer"

    def test_recording_with_modifier_prefixes_recording_engineer(self):
        assert mc._relation_role_name(_rel("recording", ["assistant"])) == "Assistant Recording Engineer"


class TestCreditRelationTypesIncludesPreviouslyDropped:
    def test_performing_orchestra_is_a_credit_type(self):
        assert "performing orchestra" in mc._CREDIT_RELATION_TYPES

    def test_sound_is_a_credit_type(self):
        assert "sound" in mc._CREDIT_RELATION_TYPES

    def test_recording_is_a_credit_type(self):
        assert "recording" in mc._CREDIT_RELATION_TYPES


class TestParseArtistCreditsIntegration:
    def test_qualifier_ordering_does_not_drop_instrument_credit(self):
        recording = {
            "artist-relation-list": [
                {
                    "type": "instrument",
                    "attribute-list": ["additional", "trumpet"],
                    "artist": {"id": "artist-1", "name": "Bill Armstrong"},
                }
            ]
        }
        credits = mc._parse_artist_credits(recording)
        assert len(credits) == 1
        assert credits[0].role_name == "Additional Trumpet"
        assert credits[0].artist_name == "Bill Armstrong"

    def test_previously_dropped_relation_types_now_parsed(self):
        recording = {
            "artist-relation-list": [
                {
                    "type": "performing orchestra",
                    "attribute-list": [],
                    "artist": {"id": "artist-2", "name": "MGM Studio Orchestra"},
                },
                {
                    "type": "sound",
                    "attribute-list": ["additional"],
                    "artist": {"id": "artist-3", "name": "Gary Lyons"},
                },
            ]
        }
        credits = mc._parse_artist_credits(recording)
        roles = {c.artist_name: c.role_name for c in credits}
        assert roles == {
            "MGM Studio Orchestra": "Performing Orchestra",
            "Gary Lyons": "Additional Sound Engineer",
        }
