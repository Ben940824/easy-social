from __future__ import annotations

from io import BytesIO

import pytest
from sqlalchemy import event

from easy_social.extensions import db
from easy_social.models import Comment, PollOption, PollVote, Post, User
from scripts.import_fake_data import DEFAULT_DATA_DIR, import_fake_data

from conftest import login, logout, register

pytestmark = pytest.mark.integration


def test_register_login_and_create_text_post(client, app):
    response = register(client, "alice")

    assert response.status_code == 200
    assert b"Feed" in response.data

    client.post("/posts", data={"body": "Hello world"}, follow_redirects=True)

    with app.app_context():
        user = User.query.filter_by(username="alice").one()
        post = Post.query.filter_by(author_id=user.id).one()
        assert post.body == "Hello world"
        assert post.media_filename is None


def test_create_image_post(client, app):
    register(client, "alice")

    response = client.post(
        "/posts",
        data={
            "body": "Picture",
            "media": (BytesIO(b"fake-image-data"), "photo.png"),
        },
        content_type="multipart/form-data",
        follow_redirects=True,
    )

    assert response.status_code == 200
    with app.app_context():
        post = Post.query.one()
        assert post.media_type == "image"
        assert post.media_filename.endswith(".png")


def test_following_adds_users_posts_to_feed(client, app):
    register(client, "alice")
    logout(client)
    register(client, "bob")
    client.post("/posts", data={"body": "Bob update"}, follow_redirects=True)
    logout(client)
    login(client, "alice")

    before_follow = client.get("/")
    assert b"Bob update" not in before_follow.data

    client.post("/users/bob/follow", follow_redirects=True)
    after_follow = client.get("/")

    assert b"Bob update" in after_follow.data
    with app.app_context():
        alice = User.query.filter_by(username="alice").one()
        bob = User.query.filter_by(username="bob").one()
        assert alice.is_following(bob)


def test_repost_and_comment(client, app):
    register(client, "alice")
    client.post("/posts", data={"body": "Original"}, follow_redirects=True)
    logout(client)
    register(client, "bob")

    with app.app_context():
        original_id = Post.query.filter_by(body="Original").one().id

    client.post(f"/posts/{original_id}/repost", follow_redirects=True)
    client.post(
        f"/posts/{original_id}/comments",
        data={"body": "Nice post"},
        follow_redirects=True,
    )

    with app.app_context():
        repost = Post.query.filter_by(repost_of_id=original_id).one()
        comment = Comment.query.one()
        assert repost.author.username == "bob"
        assert comment.body == "Nice post"
        assert comment.post_id == original_id


def test_explore_batches_comment_counts_and_follow_state(client, app):
    register(client, "alice")

    with app.app_context():
        alice = User.query.filter_by(username="alice").one()
        for index in range(8):
            user = User(username=f"user{index}", email=f"user{index}@example.com")
            user.set_password("password")
            post = Post(author=user, body=f"Post {index}")
            db.session.add_all([user, post])
            db.session.flush()
            db.session.add(Comment(author=alice, post=post, body=f"Comment {index}"))
            if index % 2 == 0:
                alice.follow(user)
        db.session.commit()

    statements = []

    def track_statement(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    with app.app_context():
        event.listen(db.engine, "before_cursor_execute", track_statement)
        try:
            response = client.get("/explore")
        finally:
            event.remove(db.engine, "before_cursor_execute", track_statement)

    assert response.status_code == 200
    assert b"Post 0" in response.data
    assert len(statements) <= 8


def test_import_fake_data_adds_comments_to_each_seed_post(app):
    with app.app_context():
        first_counts = import_fake_data(DEFAULT_DATA_DIR)
        seeded_posts = Post.query.filter_by(repost_of_id=None).all()

        assert seeded_posts
        assert first_counts["comments_created"] > 0
        for post in seeded_posts:
            assert 3 <= post.comments.count() <= 5

        comment_count = Comment.query.count()
        second_counts = import_fake_data(DEFAULT_DATA_DIR)

        assert second_counts["comments_created"] == 0
        assert Comment.query.count() == comment_count


def test_create_poll_post_and_vote_once(client, app):
    register(client, "alice")
    response = client.post(
        "/posts",
        data={
            "body": "Best editor?",
            "poll_options[]": ["Cursor", "Vim", "", ""],
        },
        follow_redirects=True,
    )
    assert response.status_code == 200

    with app.app_context():
        poll_post = Post.query.filter_by(body="Best editor?").one()
        options = PollOption.query.filter_by(post_id=poll_post.id).order_by(PollOption.position).all()
        assert len(options) == 2

    logout(client)
    register(client, "bob")
    vote_response = client.post(
        f"/posts/{poll_post.id}/vote",
        data={"option_id": options[0].id},
        follow_redirects=True,
    )
    assert vote_response.status_code == 200
    assert b"Vote submitted." in vote_response.data

    with app.app_context():
        vote = PollVote.query.filter_by(post_id=poll_post.id, user_id=User.query.filter_by(username="bob").one().id).one()
        assert vote.option_id == options[0].id


def test_user_cannot_vote_twice_on_same_poll(client, app):
    register(client, "alice")
    client.post(
        "/posts",
        data={
            "body": "Tea or coffee?",
            "poll_options[]": ["Tea", "Coffee", "", ""],
        },
        follow_redirects=True,
    )
    with app.app_context():
        poll_post = Post.query.filter_by(body="Tea or coffee?").one()
        option = PollOption.query.filter_by(post_id=poll_post.id, position=1).one()

    first_vote = client.post(
        f"/posts/{poll_post.id}/vote",
        data={"option_id": option.id},
        follow_redirects=True,
    )
    second_vote = client.post(
        f"/posts/{poll_post.id}/vote",
        data={"option_id": option.id},
        follow_redirects=True,
    )

    assert first_vote.status_code == 200
    assert second_vote.status_code == 200
    assert b"You have already voted in this poll." in second_vote.data

    with app.app_context():
        assert PollVote.query.filter_by(post_id=poll_post.id, user_id=User.query.filter_by(username="alice").one().id).count() == 1
