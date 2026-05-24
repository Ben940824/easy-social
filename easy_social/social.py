from __future__ import annotations

from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from sqlalchemy.exc import IntegrityError
from sqlalchemy import desc, func, or_
from sqlalchemy.orm import joinedload

from .extensions import db
from .media import save_media
from .models import Comment, PollOption, PollVote, Post, User, followers

bp = Blueprint("social", __name__)


def _post_query():
    return Post.query.options(
        joinedload(Post.author),
        joinedload(Post.repost_of).joinedload(Post.author),
    )


def _comment_counts_for_posts(posts: list[Post]) -> dict[int, int]:
    post_ids = {post.display_post.id for post in posts}
    if not post_ids:
        return {}

    counts = dict.fromkeys(post_ids, 0)
    rows = (
        db.session.query(Comment.post_id, func.count(Comment.id))
        .filter(Comment.post_id.in_(post_ids))
        .group_by(Comment.post_id)
        .all()
    )
    counts.update({post_id: count for post_id, count in rows})
    return counts


def _followed_user_ids(users: list[User]) -> set[int]:
    user_ids = [user.id for user in users]
    if not user_ids:
        return set()

    return {
        followed_id
        for (followed_id,) in db.session.query(followers.c.followed_id)
        .filter(
            followers.c.follower_id == current_user.id,
            followers.c.followed_id.in_(user_ids),
        )
        .all()
    }


def _poll_results_for_posts(posts: list[Post]) -> dict[int, dict]:
    post_ids = {post.display_post.id for post in posts}
    if not post_ids:
        return {}

    option_rows = (
        db.session.query(
            PollOption.post_id,
            PollOption.id,
            PollOption.option_text,
            PollOption.position,
            func.count(PollVote.id).label("vote_count"),
        )
        .outerjoin(PollVote, PollVote.option_id == PollOption.id)
        .filter(PollOption.post_id.in_(post_ids))
        .group_by(PollOption.post_id, PollOption.id, PollOption.option_text, PollOption.position)
        .order_by(PollOption.post_id, PollOption.position)
        .all()
    )

    results: dict[int, dict] = {}
    for post_id, option_id, option_text, _position, vote_count in option_rows:
        record = results.setdefault(
            post_id,
            {"total_votes": 0, "user_vote_option_id": None, "options": []},
        )
        count = int(vote_count or 0)
        record["total_votes"] += count
        record["options"].append(
            {
                "id": option_id,
                "text": option_text,
                "count": count,
            }
        )

    if current_user.is_authenticated:
        user_votes = (
            db.session.query(PollVote.post_id, PollVote.option_id)
            .filter(PollVote.user_id == current_user.id, PollVote.post_id.in_(post_ids))
            .all()
        )
        for post_id, option_id in user_votes:
            if post_id in results:
                results[post_id]["user_vote_option_id"] = option_id

    for record in results.values():
        total_votes = record["total_votes"]
        for option in record["options"]:
            option["percentage"] = (option["count"] * 100.0 / total_votes) if total_votes else 0.0

    return results


def _read_poll_option_texts() -> list[str]:
    return [option.strip() for option in request.form.getlist("poll_options[]") if option.strip()]


@bp.route("/")
@login_required
def feed():
    followed_ids = db.session.query(followers.c.followed_id).filter(
        followers.c.follower_id == current_user.id
    )
    posts = (
        _post_query()
        .filter(or_(Post.author_id == current_user.id, Post.author_id.in_(followed_ids)))
        .order_by(desc(Post.created_at))
        .limit(100)
        .all()
    )
    return render_template(
        "social/feed.html",
        posts=posts,
        comment_counts=_comment_counts_for_posts(posts),
        poll_results=_poll_results_for_posts(posts),
    )


@bp.route("/explore")
@login_required
def explore():
    posts = _post_query().order_by(desc(Post.created_at)).limit(100).all()
    users = User.query.filter(User.id != current_user.id).order_by(User.username).limit(50).all()
    return render_template(
        "social/explore.html",
        posts=posts,
        users=users,
        comment_counts=_comment_counts_for_posts(posts),
        followed_user_ids=_followed_user_ids(users),
        poll_results=_poll_results_for_posts(posts),
    )


@bp.post("/posts")
@login_required
def create_post():
    body = request.form.get("body", "").strip()
    poll_option_texts = _read_poll_option_texts()

    try:
        media_filename, media_type = save_media(request.files.get("media"))
    except ValueError as exc:
        flash(str(exc), "error")
        return redirect(request.referrer or url_for("social.feed"))

    if poll_option_texts and len(poll_option_texts) < 2:
        flash("A poll needs at least two options.", "error")
        return redirect(request.referrer or url_for("social.feed"))

    if len(poll_option_texts) > 4:
        flash("A poll can include at most four options.", "error")
        return redirect(request.referrer or url_for("social.feed"))

    if poll_option_texts and media_filename:
        flash("Poll posts cannot include media attachments.", "error")
        return redirect(request.referrer or url_for("social.feed"))

    if not body and not media_filename:
        flash("Add text, an image, or a video before posting.", "error")
        return redirect(request.referrer or url_for("social.feed"))

    post = Post(
        body=body,
        media_filename=media_filename,
        media_type=media_type,
        author=current_user,
    )
    db.session.add(post)
    db.session.flush()

    for position, option_text in enumerate(poll_option_texts, start=1):
        db.session.add(PollOption(post=post, option_text=option_text, position=position))

    db.session.commit()
    return redirect(url_for("social.feed"))


@bp.get("/posts/<int:post_id>")
@login_required
def post_detail(post_id: int):
    post = _post_query().filter(Post.id == post_id).first_or_404()
    comments = post.comments.order_by(Comment.created_at.asc()).all()
    return render_template(
        "social/post_detail.html",
        post=post,
        comments=comments,
        comment_counts={post.display_post.id: len(comments)},
        poll_results=_poll_results_for_posts([post.display_post]),
    )


@bp.post("/posts/<int:post_id>/vote")
@login_required
def vote(post_id: int):
    post = db.get_or_404(Post, post_id).display_post
    if not post.poll_options:
        flash("This post is not a poll.", "error")
        return redirect(request.referrer or url_for("social.feed"))

    selected_option = request.form.get("option_id", type=int)
    option = next((item for item in post.poll_options if item.id == selected_option), None)
    if option is None:
        flash("Please choose a valid option.", "error")
        return redirect(request.referrer or url_for("social.post_detail", post_id=post.id))

    db.session.add(PollVote(post=post, option=option, user=current_user))
    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        flash("You have already voted in this poll.", "error")
    else:
        flash("Vote submitted.", "info")

    return redirect(request.referrer or url_for("social.post_detail", post_id=post.id))


@bp.post("/posts/<int:post_id>/comments")
@login_required
def add_comment(post_id: int):
    post = db.get_or_404(Post, post_id)
    body = request.form.get("body", "").strip()
    if not body:
        flash("Comment cannot be empty.", "error")
    else:
        db.session.add(Comment(body=body, author=current_user, post=post))
        db.session.commit()
    return redirect(url_for("social.post_detail", post_id=post.id))


@bp.post("/posts/<int:post_id>/repost")
@login_required
def repost(post_id: int):
    original = db.get_or_404(Post, post_id).display_post
    if original.author_id == current_user.id:
        flash("You cannot repost your own post.", "error")
        return redirect(request.referrer or url_for("social.feed"))

    existing = Post.query.filter_by(author_id=current_user.id, repost_of_id=original.id).first()
    if existing:
        flash("You already reposted this.", "error")
        return redirect(request.referrer or url_for("social.feed"))

    db.session.add(Post(author=current_user, repost_of=original))
    db.session.commit()
    return redirect(request.referrer or url_for("social.feed"))


@bp.route("/users/<username>")
@login_required
def profile(username: str):
    user = User.query.filter_by(username=username).first_or_404()
    posts = (
        _post_query()
        .filter(Post.author_id == user.id)
        .order_by(desc(Post.created_at))
        .all()
    )
    return render_template(
        "social/profile.html",
        profile_user=user,
        posts=posts,
        comment_counts=_comment_counts_for_posts(posts),
        poll_results=_poll_results_for_posts(posts),
    )


@bp.post("/users/<username>/follow")
@login_required
def follow(username: str):
    user = User.query.filter_by(username=username).first_or_404()
    current_user.follow(user)
    db.session.commit()
    return redirect(request.referrer or url_for("social.profile", username=user.username))


@bp.post("/users/<username>/unfollow")
@login_required
def unfollow(username: str):
    user = User.query.filter_by(username=username).first_or_404()
    current_user.unfollow(user)
    db.session.commit()
    return redirect(request.referrer or url_for("social.profile", username=user.username))
