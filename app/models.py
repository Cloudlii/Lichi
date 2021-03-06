from flask import current_app, request, url_for
# 导入current_app的config['secret'],生成验证邮件token
from flask_login import UserMixin, AnonymousUserMixin
from werkzeug.security import generate_password_hash, check_password_hash
from itsdangerous import TimedJSONWebSignatureSerializer as Serializer
from . import db, login_manager
from datetime import datetime
import hashlib
from app.exceptions import ValidationError


class Permission:
    FOLLOW = 0x01
    COMMENT = 0x02
    WRITE_ARTICLES = 0x04
    MODERATE_COMMENTS = 0x08
    ADMINISTER = 0x80


class Role(db.Model):
    __tablename__ = 'roles'
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(64), unique=True)
    users = db.relationship('User', backref='role')
    default = db.Column(db.Boolean, default=False, index=True)
    permissions = db.Column(db.Integer)

    @staticmethod
    # 添加角色到数据库，初始化的时候用
    def insert_roles():
        roles = {
            'User': (Permission.FOLLOW |
                     Permission.COMMENT |
                     Permission.WRITE_ARTICLES, True),
            'Moderator': (Permission.FOLLOW |
                          Permission.COMMENT |
                         Permission.WRITE_ARTICLES |
                          Permission.MODERATE_COMMENTS, False),
            'Administrator': (0xff, False)
        }
        for r in roles:
            role = Role.query.filter_by(name=r).first()
            if role is None:
                role = Role(name=r)
            role.permissions = roles[r][0]
            role.default = roles[r][1]
            db.session.add(role)
        db.session.commit()

    def __repr__(self):
        return '<Role %r>' % self.name


class Follow(db.Model):
    __tablename__ = 'follows'
    # 关注我的人,被动
    follower_id = db.Column(db.Integer, db.ForeignKey('users.id'),
                            primary_key=True)
    # 我关注的人，主动
    followed_id = db.Column(db.Integer, db.ForeignKey('users.id'),
                            primary_key=True)
    timestamp = db.Column(db.DateTime(), default=datetime.utcnow)


class Team(db.Model):
    __tablename__ = 'teams'
    id = db.Column(db.Integer, primary_key=True)
    teammate_id = db.Column(db.Integer, db.ForeignKey('users.id'))
    post_id = db.Column(db.Integer, db.ForeignKey('posts.id'))

    def __repr__(self):
        return '<team of post %r>' % self.post_id


class Message(db.Model):
    __tablename__ = 'messages'
    id = db.Column(db.Integer, primary_key=True)
    sender_id = db.Column(db.Integer, db.ForeignKey('users.id'))
    receiver_id = db.Column(db.Integer, db.ForeignKey('users.id'))
    message = db.Column(db.Text())
    # 开关  switch True 则这条信息带有请求
    # switch = db.Column(db.BOOLEAN, default=False)
    # 已读
    readed = db.Column(db.BOOLEAN, default=False)
    # 从postContent跳转过来的暂时认定为请求加入团队
    postCont_id = db.Column(db.Integer)
    timestamp = db.Column(db.DateTime(), default=datetime.utcnow)

    def __repr__(self):
        return '<message %r>' % self.message


class User(UserMixin, db.Model):
    __tablename__ = 'users'
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(64), unique=True, index=True)
    username = db.Column(db.String(64), unique=True, index=True)
    role_id = db.Column(db.Integer, db.ForeignKey('roles.id'))
    password_hash = db.Column(db.String(128))
    avatar_hash = db.Column(db.String(32))
    confirmed = db.Column(db.Boolean, default=False)
    name = db.Column(db.String(64))
    location = db.Column(db.String(64))
    about_me = db.Column(db.Text())
    member_since = db.Column(db.DateTime(), default=datetime.utcnow)
    last_seen = db.Column(db.DateTime(), default=datetime.utcnow)
    posts = db.relationship('Post', backref='author', lazy='dynamic')
    followed = db.relationship('Follow', foreign_keys=[Follow.follower_id],
                               backref=db.backref('follower', lazy='joined'),
                               lazy='dynamic',
                               cascade='all, delete-orphan')
    followers = db.relationship('Follow', foreign_keys=[Follow.followed_id],
                                backref=db.backref('followed', lazy='joined'),
                                lazy='dynamic',
                                cascade='all, delete-orphan')

    teammate = db.relationship('Team', foreign_keys=[Team.teammate_id],
                               backref=db.backref('teammate', lazy='joined'),
                               lazy='dynamic',
                               cascade='all, delete-orphan')

    sender = db.relationship('Message', foreign_keys=[Message.sender_id],
                             backref=db.backref('sender', lazy='joined'),
                             lazy='dynamic',
                             cascade='all, delete-orphan')

    receiver = db.relationship('Message', foreign_keys=[Message.receiver_id],
                               backref=db.backref('receiver', lazy='joined'),
                               lazy='dynamic',
                               cascade='all, delete-orphan')

    # comment
    comments = db.relationship('Comment', backref='author', lazy='dynamic')
    commentLike = db.relationship('CommentLike', backref='author', lazy='dynamic')

    def __init__(self, **kwargs):
        super(User, self).__init__(**kwargs)
        if self.role is None:
            if self.email == current_app.config['FLASKY_ADMIN']:
                self.role = Role.query.filter_by(permissions=0xff).first()
            if self.role is None:
                # 算了不改成按name查找了，按字符串查找不如123查找快
                self.role = Role.query.filter_by(default=True).first()
        if self.email is not None and self.avatar_hash is None:
            self.avatar_hash = hashlib.md5(
                self.email.encode('utf-8')).hexdigest()
        self.follow(self)

    def can(self, permissions):
        return self.role is not None and \
               (self.role.permissions & permissions) == permissions

    def is_administrator(self):
        return self.can(Permission.ADMINISTER)

    # 密码
    @property
    def password(self):
        raise AttributeError('password is not a readable attribute')

    @password.setter
    def password(self, password):
        self.password_hash = generate_password_hash(password)

    # 验证密码
    def verify_password(self, password):
        return check_password_hash(self.password_hash, password)

    # 生成邮件确认token
    def generate_confirmation_token(self, expiration=3600):
        s = Serializer(current_app.config['SECRET_KEY'], expiration)
        return s.dumps({'confirm.txt': self.id})

    # 验证邮件token
    def confirm(self, token):
        s = Serializer(current_app.config['SECRET_KEY'])
        try:
            data = s.loads(token)
        except:
            return False
        if data.get('confirm.txt') != self.id:
            return False
        self.confirmed = True
        db.session.add(self)
        return True

    def generate_reset_token(self, expiration=3600):
        s = Serializer(current_app.config['SECRET_KEY'], expiration)
        return s.dumps({'reset': self.id})

    def reset_password(self, token, newpassword):
        s = Serializer(current_app.config['SECRET_KEY'])
        try:
            data = s.loads(token)
        except:
            return False
        if data.get('reset') != self.id:
            return False
        self.password = newpassword
        db.session.add(self)
        return True

    def generate_email_token(self, new_email, expiration=3600):
        s = Serializer(current_app.config['SECRET_KEY'], expiration)
        return s.dumps({'change_email': self.id, 'new_email': new_email})

    def generate_email_change_token(self, new_email, expiration=3600):
        s = Serializer(current_app.config['SECRET_KEY'], expiration)
        return s.dumps({'change_email': self.id, 'new_email': new_email})

    def change_email(self, token):
        s = Serializer(current_app.config['SECRET_KEY'])
        try:
            data = s.loads(token)
        except:
            return False
        if data.get('change_email') != self.id:
            return False
        new_email = data.get('new_email')
        if new_email is None:
            return False
        if self.query.filter_by(email=new_email).first() is not None:
            return False
        self.email = new_email
        self.avatar_hash = hashlib.md5(self.email.encode('utf-8')).hexdigest()
        db.session.add(self)
        return True

    # 刷新上次上线时间
    def ping(self):
        self.last_seen = datetime.utcnow()
        db.session.add(self)

    def gravatar(self, size=100, default='retro', rating='x'):
        if request.is_secure:
            url = 'https://secure.gravatar.com/avatar'
        else:
            url = 'http://www.gravatar.com/avatar'
        hash = hashlib.md5(self.email.encode('utf-8')).hexdigest()
        return '{url}/{hash}?s={size}&d={default}&r={rating}'.format(
            url=url, hash=hash, size=size, default=default, rating=rating)

    # 生成僵尸用户
    @staticmethod
    def generate_fake(count=100):
        from sqlalchemy.exc import IntegrityError
        from random import seed
        import forgery_py

        seed()
        for i in range(count):
            u = User(email=forgery_py.internet.email_address(),
                     username=forgery_py.internet.user_name(True),
                     password=forgery_py.lorem_ipsum.word(),
                     confirmed=True,
                     name=forgery_py.name.full_name(),
                     location=forgery_py.address.city(),
                     about_me=forgery_py.lorem_ipsum.sentence(),
                     member_since=forgery_py.date.date(True))
            db.session.add(u)
            try:
                db.session.commit()
            except IntegrityError:
                db.session.rollback()

    def is_following(self, user):
        return self.followed.filter_by(
            followed_id=user.id).first() is not None

    def follow(self, user):
        if not self.is_following(user):
            # 主动关注user
            f = Follow(follower=self, followed=user)
            db.session.add(f)

    def unfollow(self, user):
        f = self.followed.filter_by(followed_id=user.id).first()
        if f:
            db.session.delete(f)

    def is_followed_by(self, user):
        return self.followers.filter_by(
            follower_id=user.id).first() is not None

    @staticmethod
    def add_self_follows():
        for user in User.query.all():
            if not user.is_following(user):
                user.follow(user)
                db.session.add(user)
                db.session.commit()

    @property
    def followed_posts(self):
        return Post.query.join(Follow, Follow.followed_id == Post.author_id) \
            .filter(Follow.follower_id == self.id)

    def generate_auth_token(self, expiration):
        s = Serializer(current_app.config['SECRET_KEY'],
                       expires_in=expiration)
        return s.dumps({'id': self.id})

    @staticmethod
    def verify_auth_token(token):
        s = Serializer(current_app.config['SECRET'])
        try:
            data = s.loads(token)
        except:
            return None
        return User.query.get(data['id'])

    def to_json(self):
        json_user = {
            'url': url_for('api.get_user', id=self.id, _external=True),
            'username': self.username,
            'member_since': self.member_since,
            'last_seen': self.last_seen,
            'posts': url_for('api.get_user_posts', id=self.id, _external=True),
            'post_count': self.posts.count()
        }
        return json_user

    def send_message(self, user, message, **kwargs):
        m = Message(sender=self,
                    receiver=user,
                    postCont_id = kwargs.get('postCont_id'),
                    message=message)
        db.session.add(m)

    def __repr__(self):
        return '<User %r>' % self.username


class AnonymousUser(AnonymousUserMixin):
    def can(self, permissions):
        return False

    def is_administrator(self):
        return False


@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))


login_manager.anonymous_user = AnonymousUser


class Post(db.Model):
    __tablename__ = 'posts'
    id = db.Column(db.Integer, primary_key=True)
    author_id = db.Column(db.Integer, db.ForeignKey('users.id'))
    title = db.Column(db.Text())
    postContents = db.relationship('PostContent', backref='post', lazy='dynamic')
    team = db.relationship('Team', backref='post', uselist=False)
    created_time = db.Column(db.DateTime, index=True, default=datetime.utcnow)
    last_updated_time = db.Column(db.DateTime, index=True, default=datetime.utcnow)

    def ping(self):
        self.last_seen = datetime.utcnow()
        db.session.add(self)

    # 就一个时间 没什么好展示的，author可以通过postContent.post.author访问
    # def to_json(self):
    #     json_post = {
    #         'url': url_for('api.get_post', id=self.id, _external=True),
    #         'author': url_for('api.get_user', id=self.author_id, _external=True),
    #         'posts': url_for('api.get_post', id=self.post.id, _external=True),
    #         'post_count': self.posts.count()
    #     }
    #     return json_post

    def __repr__(self):
        return '<Post %r>' % self.id


class PostContent(db.Model):
    # 不同版本的post
    __tablename__ = 'postContents'
    id = db.Column(db.Integer, primary_key=True)
    version = db.Column(db.Integer, index=True, default=1, unique=True)
    version_intro = db.Column(db.Text, )
    timestamp = db.Column(db.DateTime, index=True, default=datetime.utcnow)

    # Content
    body = db.Column(db.Text)
    post_id = db.Column(db.Integer, db.ForeignKey('posts.id'))
    # comment
    comments = db.relationship('Comment', backref='postContent', lazy='dynamic')
    postContentLikes = db.relationship('PostContentLike', backref='postContentLike', lazy='dynamic')

    # def __init__(self, **kwargs):
    #     super(PostContent, self).__init__(**kwargs)
    # #     #创建postContent时查找其链接的Post，查找到并链接上
    # #     if self.post_id is None:
    # #         self.post_id = Post.query.filter_by(post_id=kwargs.get('post_id'))
    # #         post = Post.query.filter_by(self.post_id).first()
    # #         post.last_updated_time = datetime.utcnow()
    # #
    # #         db.session.add(post)
    # #         db.session.add(self)
    # #         db.session.commit()
    #     # if self.post_id:
    #     #     self.post_id = Post
    #     #     self.version = post.postContents.all()[-1].version + 1

    @staticmethod
    def generate_fake(count=100):
        from random import seed, randint
        import forgery_py

        seed()
        user_count = User.query.count()
        for i in range(count):
            u = User.query.offset(randint(0, user_count - 1)).first()
            p = Post(author=u)
            db.session.add(p)
            db.session.commit()
            pc = PostContent(body=forgery_py.lorem_ipsum.sentences(randint(1, 3)),
                             version_intro=forgery_py.lorem_ipsum.sentences(randint(1, 3)),
                             timestamp=forgery_py.date.date(True),
                             post_id=p.id)
            db.session.add(pc)
            db.session.commit()

    def to_json(self):
        json_postContent = {
            'url': url_for('api.get_postContent', id=self.id, _external=True),
            'body': self.body,
            'timestamp': self.timestamp,
            'author': url_for('api.get_user', id=self.post.author_id,
                              _external=True),
            'comments': url_for('api.get_postContent_comments', id=self.id, _external=True),
            'comment_count': self.comments.count()
        }
        return json_postContent

    @staticmethod
    def from_json(json_postContent):
        body = json_postContent.get('body')
        if body is None or body == '':
            raise ValidationError('post does not have a body')
        return PostContent(body=body)


    def totalLikes(self):
        total = 0
        for l in PostContentLike.query.filter_by(postContent_id=self.id).all():
            if l.like:
                total += 1
            if l.dislike:
                total -= 1
        return total


    def __repr__(self):
        return '<postContent %r>' % self.version_intro


class Comment(db.Model):
    __tablename__ = 'comments'
    id = db.Column(db.Integer, primary_key=True)
    body = db.Column(db.Text)
    timestamp = db.Column(db.DateTime, index=True, default=datetime.utcnow)
    disabled = db.Column(db.BOOLEAN)
    autor_id = db.Column(db.Integer, db.ForeignKey('users.id'))
    postContent_id = db.Column(db.Integer, db.ForeignKey('postContents.id'))
    commentLikes = db.relationship('CommentLike', backref='commentLike', lazy='dynamic')

    def to_json(self):
        json_comment = {
            'url': url_for('api.get_comment', id=self.id, _external=True),
            'postContent': url_for('api.get_postContent', id=self.postContent_id, _external=True),
            'body': self.body,
            'timestamp': self.timestamp,
            'author': url_for('api.get_user', id=self.postContent.post.author_id,
                              _external=True)
        }
        return json_comment

    @staticmethod
    def from_json(json_comment):
        body = json_comment.get('body')
        if body is None or body == '':
            raise ValidationError('comment does not have a body')
        return Comment(body=body)


    def __repr__(self):
        return '<comment %r>' % self.body


class CommentLike(db.Model):
    __tablename__ = 'commentLikes'
    id = db.Column(db.Integer, primary_key=True)
    comment_id = db.Column(db.Integer, db.ForeignKey('comments.id'))
    author_id = db.Column(db.Integer, db.ForeignKey('users.id'))

    def __repr__(self):
        return '<CommentLike to %r>' % self.id


class PostContentLike(db.Model):
    __tablename__ = 'postContentLikes'
    id = db.Column(db.Integer, primary_key=True)
    postContent_id = db.Column(db.Integer, db.ForeignKey('postContents.id'))
    author_id = db.Column(db.Integer, db.ForeignKey('users.id'))
    like = db.Column(db.BOOLEAN)
    dislike = db. Column(db.BOOLEAN)


    def __repr__(self):
        return '<PostContentLike to %r>' % self.id