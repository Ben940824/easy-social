from __future__ import annotations

from sqlalchemy.exc import IntegrityError

import pytest

from easy_social.extensions import db
from easy_social.models import PollOption, PollVote, Post, User

pytestmark = pytest.mark.unit


def make_user(username: str) -> User:
    user = User(username=username, email=f"{username}@example.com")
    user.set_password("password")
    return user


def test_user_password_hashing_round_trip(app):
    with app.app_context():
        user = make_user("alice")

        assert user.password_hash != "password"
        assert user.check_password("password")
        assert not user.check_password("wrong-password")


def test_follow_is_idempotent_and_blocks_self_follow(app):
    with app.app_context():
        alice = make_user("alice")
        bob = make_user("bob")
        db.session.add_all([alice, bob])
        db.session.commit()

        alice.follow(alice)
        alice.follow(bob)
        alice.follow(bob)
        db.session.commit()

        assert not alice.is_following(alice)
        assert alice.is_following(bob)
        assert alice.following.count() == 1


def test_unfollow_removes_existing_follow_only(app):
    with app.app_context():
        alice = make_user("alice")
        bob = make_user("bob")
        db.session.add_all([alice, bob])
        db.session.commit()

        alice.unfollow(bob)
        alice.follow(bob)
        db.session.commit()

        alice.unfollow(bob)
        alice.unfollow(bob)
        db.session.commit()

        assert not alice.is_following(bob)
        assert alice.following.count() == 0


def test_post_requires_content_media_or_repost(app):
    with app.app_context():
        alice = make_user("alice")
        db.session.add(alice)
        db.session.commit()

        db.session.add(Post(author=alice, body=""))

        try:
            db.session.commit()
        except IntegrityError:
            db.session.rollback()
        else:
            raise AssertionError("empty post should violate content constraint")


def test_repost_display_post_points_to_original(app):
    with app.app_context():
        alice = make_user("alice")
        bob = make_user("bob")
        original = Post(author=alice, body="Original post")
        repost = Post(author=bob, body="", repost_of=original)
        db.session.add_all([alice, bob, original, repost])
        db.session.commit()

        assert not original.is_repost
        assert original.display_post == original
        assert repost.is_repost
        assert repost.display_post == original


def test_poll_vote_unique_per_user_per_post(app):
    with app.app_context():
        alice = make_user("alice")
        bob = make_user("bob")
        poll_post = Post(author=alice, body="Which one?")
        db.session.add_all([alice, bob, poll_post])
        db.session.flush()

        option_a = PollOption(post=poll_post, option_text="A", position=1)
        option_b = PollOption(post=poll_post, option_text="B", position=2)
        db.session.add_all([option_a, option_b])
        db.session.flush()

        db.session.add(PollVote(post=poll_post, option=option_a, user=bob))
        db.session.commit()

        db.session.add(PollVote(post=poll_post, option=option_b, user=bob))
        try:
            db.session.commit()
        except IntegrityError:
            db.session.rollback()
        else:
            raise AssertionError("duplicate vote per user per poll should violate unique constraint")
