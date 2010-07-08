from sqlalchemy import *
from sqlalchemy.exc import OperationalError, NoSuchTableError
from sqlalchemy.ext.sqlsoup import SqlSoup
from sqlalchemy.orm import mapper, create_session, relation
import datetime
import logging
import os

log = logging.getLogger(__name__)
path = '%s/data.db' % os.path.abspath(os.path.curdir)

engine = create_engine('sqlite:///%s' % path)
metadata = MetaData(engine)
Session = create_session(bind = engine, autoflush = True)

# DB VERSION
latestDatabaseVersion = 2

dbVersionTable = Table('DbVersion', metadata,
                     Column('version', Integer, primary_key = True)
            )

movieTable = Table('Movie', metadata,
                     Column('id', Integer, primary_key = True),
                     Column('dateAdded', DateTime(), default = datetime.datetime.utcnow),
                     Column('name', String()),
                     Column('year', Integer),
                     Column('imdb', String()),
                     Column('status', String()),
                     Column('quality', String(), ForeignKey('QualityTemplate.id')),
                     Column('movieDb', String())
            )

movieQueueTable = Table('MovieQueue', metadata,
                     Column('id', Integer, primary_key = True),
                     Column('movieId', Integer, ForeignKey('Movie.id')),
                     Column('qualityType', String()),
                     Column('date', DateTime(), default = datetime.datetime.utcnow),
                     Column('order', Integer),
                     Column('active', Boolean),
                     Column('completed', Boolean),
                     Column('waitFor', Integer, default = 0),
                     Column('markComplete', Boolean),
                     Column('name', String()),
                     Column('link', String())
            )

renameHistoryTable = Table('RenameHistory', metadata,
                     Column('id', Integer, primary_key = True),
                     Column('movieQueue', Integer, ForeignKey('MovieQueue.id')),
                     Column('old', String()),
                     Column('new', String())
            )

qualityTemplateTable = Table('QualityTemplate', metadata,
                     Column('id', Integer, primary_key = True),
                     Column('name', Integer, unique = True),
                     Column('label', String()),
                     Column('order', Integer),
                     Column('waitFor', Integer, default = 0),
                     Column('custom', Boolean),
                     Column('default', Boolean)
            )

qualityTemplateTypeTable = Table('QualityTemplateType', metadata,
                     Column('id', Integer, primary_key = True),
                     Column('quality', Integer, ForeignKey('QualityTemplate.id')),
                     Column('order', Integer),
                     Column('type', String()),
                     Column('markComplete', Boolean)
            )

class DbVersion(object):
    def __init__(self, version):
        self.version = version

    def __repr__(self):
        return "<dbversion: %s" % self.version

class Movie(object):
    name = None
    status = None

    def __repr__(self):
        return "<movie: %s" % self.name

class MovieQueue(object):
    active = None
    complete = None
    order = None

    def __repr__(self):
        return "<moviequeue: %s active=%s complete=%s" % (self.Movie.name, self.active, self.complete)

class RenameHistory(object):
    def __repr__(self):
        return "<renamehistory: %s" % self.name

class QualityTemplate(object):
    id = None
    name = None
    order = None
    custom = None
    
    def __repr__(self):
        return self.name

class QualityTemplateType(object):
    order = None

    def __repr__(self):
        return "<qualitytempatetypes: %s" % self.type

# Mappers
versionMapper = mapper(DbVersion, dbVersionTable)
movieMapper = mapper(Movie, movieTable, properties = {
   'queue': relation(MovieQueue, backref = 'Movie', primaryjoin =
                and_(movieQueueTable.c.movieId == movieTable.c.id,
                movieQueueTable.c.active == True), order_by = movieQueueTable.c.order, lazy = 'joined'),
   'template': relation(QualityTemplate, backref = 'Movie')
})
movieQueueMapper = mapper(MovieQueue, movieQueueTable)
renameHistoryMapper = mapper(RenameHistory, renameHistoryTable)
qualityMapper = mapper(QualityTemplate, qualityTemplateTable, properties = {
   'types': relation(QualityTemplateType, backref = 'QualityTemplate', order_by = qualityTemplateTypeTable.c.order, lazy = 'joined')
})
qualityCustomMapper = mapper(QualityTemplateType, qualityTemplateTypeTable)

def initDb():
    log.info('Initializing Database.')

    # DB exists, do upgrade
    if os.path.isfile(path):
        doUpgrade = True;
    else:
        doUpgrade = False

    metadata.create_all()

    # set default qualities
    from app.lib.qualities import Qualities
    qu = Qualities()
    qu.initDefaults()

    if doUpgrade:
        upgradeDb()
    else:
        for nr in range(1, latestDatabaseVersion + 1):
            Session.add(DbVersion(nr))

def upgradeDb():

    currentVersion = Session.query(DbVersion).order_by(desc(DbVersion.version)).first()
    if currentVersion and currentVersion.version == latestDatabaseVersion:
        log.debug('Database is up to date.')
        return

    # Version 1 -> 2
    version2 = Session.query(DbVersion).filter_by(version = 2).first()
    if not version2: migrateVersion2()

def migrateVersion2():
    log.info('Upgrading DB to version 2.')

    # for some normal executions
    db = SqlSoup(engine)

    # Remove not used table
    try:
        db.execute('DROP TABLE Feed')
        log.info('Removed old Feed table.')
    except (OperationalError, NoSuchTableError):
        log.debug('No Feed table found.')

    # History add column
    try:
        db.execute('DROP TABLE History')
        log.info('Removed History table.')
    except (OperationalError, NoSuchTableError):
        log.debug('No History table found.')

    # RenameHistory add column
    try:
        Session.query(RenameHistory).filter_by(movieQueue = '').all()
        log.debug('Column "RenameHistory:movieQueue" exists, not necessary.')
    except (OperationalError, NoSuchTableError):
        db.execute("CREATE TABLE RenameHistoryBackup(id, movieId, old, new);")
        db.execute("INSERT INTO RenameHistoryBackup SELECT id, movieId, old, new FROM RenameHistory;")
        db.execute("DROP TABLE RenameHistory;")
        db.execute("CREATE TABLE RenameHistory (id, movieQueue, old VARCHAR, new VARCHAR);")
        db.execute("INSERT INTO RenameHistory SELECT id, movieId, old, new FROM RenameHistoryBackup;")
        db.execute("DROP TABLE RenameHistoryBackup;")
        log.info('Added "movieQueue" column to existing RenameHistory Table.')

    # Mark all history

    # Quality from string to int
    movies = Session.query(Movie).all()
    for movie in movies:

        # Add moviequeues
        log.info('Making Queue item for %s' % movie.name)
        queue = MovieQueue()
        queue.movieId = movie.id
        queue.qualityType = movie.quality
        queue.order = 1
        queue.active = (movie.status != u'deleted')
        queue.completed = (movie.status != u'want')
        queue.markComplete = True
        Session.add(queue)

        log.info('Doing some stuff to RenameHistory')
        history = Session.query(RenameHistory).filter_by(movieQueue = movie.id).first()
        if history:
            history.movieQueue = queue.id
            queue.name = os.path.basename(os.path.dirname(history.old))

    Session.add(DbVersion(1)) # Add version 1 for teh nice
    Session.add(DbVersion(2))
