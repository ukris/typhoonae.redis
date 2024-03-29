# -*- coding: utf-8 -*-
#
# Copyright 2010 Tobias Rodäbel
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""Unit tests for the Datastore Redis stub."""

from google.appengine.api import datastore_types
from google.appengine.datastore import datastore_index
from google.appengine.ext import db

import datetime
import google.appengine.api.apiproxy_stub
import google.appengine.api.apiproxy_stub_map
import google.appengine.api.datastore_admin
import google.appengine.api.datastore_errors
import google.appengine.api.users
import google.appengine.datastore.entity_pb
import google.appengine.runtime.apiproxy_errors
import os
import time
import threading
import typhoonae.redis.datastore_redis_stub
import unittest


class DatastoreRedisTestCaseBase(unittest.TestCase):
    """Base class for testing the TyphoonAE Datastore Redis API proxy stub."""

    def setUp(self):
        """Sets up test environment and regisers stub."""

        # Set required environment variables
        os.environ['APPLICATION_ID'] = 'test'
        os.environ['AUTH_DOMAIN'] = 'mydomain.local'
        os.environ['USER_EMAIL'] = 'tester@mydomain.local'
        os.environ['USER_IS_ADMIN'] = '1'

        # Read index definitions.
        index_yaml = open(
            os.path.join(os.path.dirname(__file__), 'index.yaml'), 'r')

        try:
            indexes = datastore_index.IndexDefinitionsToProtos(
                'test',
                datastore_index.ParseIndexDefinitions(index_yaml).indexes)
        except TypeError:
            indexes = []

        index_yaml.close()

        # Register API proxy stub.
        google.appengine.api.apiproxy_stub_map.apiproxy = (
            google.appengine.api.apiproxy_stub_map.APIProxyStubMap())

        datastore = typhoonae.redis.datastore_redis_stub.DatastoreRedisStub(
            'test', indexes)

        try:
            google.appengine.api.apiproxy_stub_map.apiproxy.RegisterStub(
                'datastore_v3', datastore)
        except google.appengine.runtime.apiproxy_errors.ApplicationError, e:
            raise RuntimeError('These tests require a running Redis server '
                               '(%s)' % e)

        self.stub = google.appengine.api.apiproxy_stub_map.apiproxy.GetStub(
            'datastore_v3')

    def tearDown(self):
        """Clears all data."""

        self.stub.Clear()


class StoredEntityTestCase(DatastoreRedisTestCaseBase):
    """Testing entity wrapper class."""

    def testStoredEntity(self):
        """Initializes a stored entity instance."""

        class MyModel(db.Model):
            contents = db.StringProperty()

        key = MyModel(contents="Some contents.").save()

        entity = db.get(key)

        protobuf = db.model_to_protobuf(entity)

        stored_entity = typhoonae.redis.datastore_redis_stub._StoredEntity(
            protobuf)

        self.assertEqual(protobuf, stored_entity.protobuf)

        self.assertEqual(
            'j\x15j\x04testr\r\x0b\x12\x07MyModel\x18\x01\x0cr\x1e\x1a\x08'
            'contents \x00*\x10\x1a\x0eSome contents.\x82\x01\r\x0b\x12\x07'
            'MyModel\x18\x01\x0c',
            stored_entity.encoded_protobuf)

        self.assertEqual({u'contents': u'Some contents.'}, stored_entity.native)

        self.assertTrue(
            isinstance(
                stored_entity.key(),
                google.appengine.datastore.entity_pb.Reference))


class DatastoreRedisTestCase(DatastoreRedisTestCaseBase):
    """Testing the TyphoonAE Datastore Redis API proxy stub."""

    def testStub(self):
        """Tests whether our stub is registered."""

        self.assertNotEqual(None, self.stub)

    def testConnectionError(self):
        """Tries to connect to wrong host and port."""

        self.assertRaises(
            google.appengine.runtime.apiproxy_errors.ApplicationError,
            typhoonae.redis.datastore_redis_stub.DatastoreRedisStub,
            'test', [], host='nowhere', port=10987)

    def test__ValidateAppId(self):
        """Validates an application id."""

        self.assertRaises(
            google.appengine.api.datastore_errors.BadRequestError,
            self.stub._DatastoreRedisStub__ValidateAppId,
            'foo')

    def test_GetAppIdNamespaceKindForKey(self):
        """Gets encoded app and kind from given key."""

        ref = google.appengine.datastore.entity_pb.Reference()
        ref.set_app(u'test')
        ref.set_name_space(u'namespace')
        path = ref.mutable_path()
        elem = path.add_element()
        elem.set_type('Foo')
        elem = path.add_element()
        elem.set_type('Bar')

        self.assertEqual(
            u'test!namespace\x08Bar',
            self.stub._GetAppIdNamespaceKindForKey(ref))

    def test_GetKeyForRedisKey(self):
        """Inititalizes an entity_pb.Reference from a Redis key."""

        key = self.stub._GetKeyForRedisKey(
            u'test!Foo\x08\t0000000000002\x07Bar\x08bar')

        self.assertEqual(
            datastore_types.Key.from_path(
                u'Foo', 2, u'Bar', u'bar', _app=u'test'),
            key)

    def test_GetRedisKeyForKey(self):
        """Creates a valid Redis key."""

        ref = google.appengine.datastore.entity_pb.Reference()
        ref.set_app(u'test')
        ref.set_name_space(u'namespace')
        path = ref.mutable_path()
        elem = path.add_element()
        elem.set_type('Foo')
        elem.set_id(1)
        elem = path.add_element()
        elem.set_type('Bar')
        elem.set_id(2)

        self.assertEqual(
            u'test!Foo\x08\t0000000000001\x07Bar\x08\t0000000000002',
            self.stub._GetRedisKeyForKey(ref))

    def testPutGetDelete(self):
        """Puts/gets/deletes entities into/from the datastore."""

        class Author(db.Model):
            name = db.StringProperty()

        class Book(db.Model):
            title = db.StringProperty()

        a = Author(name='Mark Twain', key_name='marktwain')
        a.put()

        b = Book(parent=a, title="The Adventures Of Tom Sawyer")
        b.put()

        key = b.key()

        del a, b

        book = google.appengine.api.datastore.Get(key)
        self.assertEqual(
            "{u'title': u'The Adventures Of Tom Sawyer'}", str(book))

        author = google.appengine.api.datastore.Get(book.parent())
        self.assertEqual("{u'name': u'Mark Twain'}", str(author))

        del book

        google.appengine.api.datastore.Delete(key)

        self.assertRaises(
            google.appengine.api.datastore_errors.EntityNotFoundError,
            google.appengine.api.datastore.Get,
            key)

        del author

        mark_twain = Author.get_by_key_name('marktwain')

        self.assertEqual('Author', mark_twain.kind())
        self.assertEqual('Mark Twain', mark_twain.name)

        mark_twain.delete()

    def testGetEntitiesByNameAndID(self):
        """Tries to retrieve entities by name or numeric id."""

        class Book(db.Model):
            title = db.StringProperty()

        Book(title="The Hitchhiker's Guide to the Galaxy").put()
        book = Book.get_by_id(1)
        self.assertEqual("The Hitchhiker's Guide to the Galaxy", book.title)

        Book(key_name="solong",
             title="So Long, and Thanks for All the Fish").put()
        book = Book.get_by_key_name("solong")
        self.assertEqual("So Long, and Thanks for All the Fish", book.title)

    def testLocking(self):
        """Acquires and releases transaction locks."""

        self.stub._AcquireLockForEntityGroup('foo', timeout=1)
        self.stub._ReleaseLockForEntityGroup('foo')

        self.stub._AcquireLockForEntityGroup('bar', timeout=2)
        t = time.time()
        self.stub._AcquireLockForEntityGroup('bar', timeout=1)
        assert time.time() > t + 1
        self.stub._ReleaseLockForEntityGroup('bar')

    def testTransactions(self):
        """Executes 1000 transactions in 10 concurrent threads."""

        class Counter(db.Model):
            value = db.IntegerProperty()

        counter = Counter(key_name='counter', value=0)
        counter.put()

        del counter

        class Incrementer(threading.Thread):
            def run(self):
                def tx():
                    counter = Counter.get_by_key_name('counter')
                    counter.value += 1
                    counter.put()
                for i in range(100):
                    db.run_in_transaction(tx)

        incrementers = []
        for i in range(10):
            incrementers.append(Incrementer())
            incrementers[i].start()

        for incr in incrementers:
            incr.join()

        counter = Counter.get_by_key_name('counter')
        self.assertEqual(1000, counter.value)

    def testLargerTransaction(self):
        """Executes multiple operations in one transaction."""

        class Author(db.Model):
            name = db.StringProperty()

        class Book(db.Model):
            title = db.StringProperty()

        def tx():
            a = Author(name='Mark Twain', key_name='marktwain')
            a.put()

            b = Book(parent=a, title="The Adventures Of Tom Sawyer")
            b.put()

            b.delete()

        db.run_in_transaction(tx)

        self.assertEqual(1, Author.all().count())
        self.assertEqual(0, Book.all().count())

        marktwain = Author.get_by_key_name('marktwain')

        def query_tx():
            query = db.Query()
            query.filter('__key__ = ', marktwain.key())
            author = query.get()

        self.assertRaises(
            google.appengine.api.datastore_errors.BadRequestError,
            db.run_in_transaction, query_tx)

    def testKindlessAncestorQueries(self):
        """Perform kindless queries for entities with a given ancestor."""

        class Author(db.Model):
            name = db.StringProperty()

        class Book(db.Model):
            title = db.StringProperty()

        author = Author(name='Mark Twain', key_name='marktwain').put()

        book = Book(parent=author, title="The Adventures Of Tom Sawyer").put()

        query = db.Query()
        query.ancestor(author)
        query.filter('__key__ = ', book)

        self.assertEqual(book, query.get().key())

        book = query.get()
        book.delete()

        self.assertEqual(0, query.count())

    def testRunQuery(self):
        """Runs some simple queries."""

        class Employee(db.Model):
            first_name = db.StringProperty(required=True)
            last_name = db.StringProperty(required=True)
            manager = db.SelfReferenceProperty()

        manager = Employee(first_name='John', last_name='Dowe')
        manager.put()

        employee = Employee(
            first_name=u'John', last_name='Appleseed', manager=manager.key())
        employee.put()

        # Perform a very simple query.
        query = Employee.all()
        self.assertEqual(set(['John Dowe', 'John Appleseed']),
                         set(['%s %s' % (e.first_name, e.last_name)
                              for e in query.run()]))

        # Rename the manager.
        manager.first_name = 'Clara'
        manager.put()

        # And perform the same query as above.
        query = Employee.all()
        self.assertEqual(set(['Clara Dowe', 'John Appleseed']),
                         set(['%s %s' % (e.first_name, e.last_name)
                              for e in query.run()]))

        # Get only one entity.
        query = Employee.all()
        self.assertEqual(u'Dowe', query.get().last_name)
        self.assertEqual(u'Dowe', query.fetch(1)[0].last_name)

        # Delete our entities.
        employee.delete()
        manager.delete()

        # Our query results should now be empty.
        query = Employee.all()
        self.assertEqual([], list(query.run()))

    def testCount(self):
        """Counts query results."""

        class Balloon(db.Model):
            color = db.StringProperty()

        Balloon(color='Red').put()

        self.assertEqual(1, Balloon.all().count())

        Balloon(color='Blue').put()

        self.assertEqual(2, Balloon.all().count())

    def testQueryWithFilter(self):
        """Tries queries with filters."""

        class SomeKind(db.Model):
            value = db.StringProperty()

        foo = SomeKind(value="foo")
        foo.put()

        bar = SomeKind(value="bar")
        bar.put()

        class Artifact(db.Model):
            description = db.StringProperty(required=True)
            age = db.IntegerProperty()

        vase = Artifact(description="Mycenaean stirrup vase", age=3300)
        vase.put()

        helmet = Artifact(description="Spartan full size helmet", age=2400)
        helmet.put()

        unknown = Artifact(description="Some unknown artifact")
        unknown.put()

        query = Artifact.all().filter('age =', 2400)

        self.assertEqual(
            ['Spartan full size helmet'],
            [artifact.description for artifact in query.run()])

        query = db.GqlQuery("SELECT * FROM Artifact WHERE age = :1", 3300)

        self.assertEqual(
            ['Mycenaean stirrup vase'],
            [artifact.description for artifact in query.run()])

        query = Artifact.all().filter('age IN', [2400, 3300])

        self.assertEqual(
            set(['Spartan full size helmet', 'Mycenaean stirrup vase']),
            set([artifact.description for artifact in query.run()]))

        vase.delete()

        query = Artifact.all().filter('age IN', [2400])

        self.assertEqual(
            ['Spartan full size helmet'],
            [artifact.description for artifact in query.run()])

        helmet.age = 2300
        helmet.put()

        query = Artifact.all().filter('age =', 2300)

        self.assertEqual([2300], [artifact.age for artifact in query.run()])

        query = Artifact.all()

        self.assertEqual(
            set([2300L, None]),
            set([artifact.age for artifact in query.run()]))

    def testQueryForKeysOnly(self):
        """Queries for entity keys instead of full entities."""

        class Asset(db.Model):
            name = db.StringProperty(required=True)
            price = db.FloatProperty(required=True)

        lamp = Asset(name="Bedside Lamp", price=10.45)
        lamp.put()

        towel = Asset(name="Large Towel", price=3.50)
        towel.put()

        query = Asset.all(keys_only=True)

        self.assertEqual(
            set([
                datastore_types.Key.from_path(u'Asset', 1, _app=u'test'),
                datastore_types.Key.from_path(u'Asset', 2, _app=u'test')]),
            set(query.run()))

    def testQueryWithOrder(self):
        """Tests queries with sorting."""

        class Planet(db.Model):
            name = db.StringProperty()
            moon_count = db.IntegerProperty()
            distance = db.FloatProperty()

        earth = Planet(name="Earth", distance=93.0, moon_count=1)
        earth.put()

        saturn = Planet(name="Saturn", distance=886.7, moon_count=18)
        saturn.put()

        venus = Planet(name="Venus", distance=67.2, moon_count=0)
        venus.put()

        mars = Planet(name="Mars", distance=141.6, moon_count=2)
        mars.put()

        mercury = Planet(name="Mercury", distance=36.0, moon_count=0)
        mercury.put()

        query = (Planet.all()
            .filter('moon_count <', 10)
            .order('moon_count')
            .order('-name')
            .order('distance'))

        self.assertEqual(
            [u'Venus', u'Mercury', u'Earth', u'Mars'],
            [planet.name for planet in query.run()]
        )

        query = Planet.all().filter('distance >', 100).order('-distance')

        self.assertEqual(
            ['Saturn', 'Mars'],
            [planet.name for planet in query.run()]
        )

        query = Planet.all().filter('distance <=', 93).order('distance')

        self.assertEqual(
            ['Mercury', 'Venus', 'Earth'],
            [planet.name for planet in query.run()]
        )

        query = (Planet.all()
            .filter('distance >', 80.0)
            .filter('distance <', 150)
            .order('distance'))

        self.assertEqual(
            ['Earth', 'Mars'],
            [planet.name for planet in query.run()])

        query = Planet.all().filter('distance >=', 93.0).order('distance')
        self.assertEqual(
            [u'Earth', u'Mars', u'Saturn'],
            [planet.name for planet in query.run()])

        query = Planet.all().filter('distance ==', 93.0)
        self.assertEqual(
            [u'Earth'], [planet.name for planet in query.run()])

    def testQueriesWithMultipleFiltersAndOrders(self):
        """Tests queries with multiple filters and orders."""

        class Artist(db.Model):
            name = db.StringProperty()

        class Album(db.Model):
            title = db.StringProperty()

        class Song(db.Model):
            artist = db.ReferenceProperty(Artist)
            album = db.ReferenceProperty(Album)
            duration = db.StringProperty()
            genre = db.CategoryProperty()
            title = db.StringProperty()

        beatles = Artist(name="The Beatles")
        beatles.put()

        abbeyroad = Album(title="Abbey Road")
        abbeyroad.put()

        herecomesthesun = Song(
            artist=beatles.key(),
            album=abbeyroad.key(),
            duration="3:06",
            genre=db.Category("Pop"),
            title="Here Comes The Sun")
        herecomesthesun.put()

        query = (Song.all()
            .filter('artist =', beatles)
            .filter('album =', abbeyroad))

        self.assertEqual(u'Here Comes The Sun', query.get().title)

        cometogether = Song(
            artist=beatles.key(),
            album=abbeyroad.key(),
            duration="4:21",
            genre=db.Category("Pop"),
            title="Come Together")
        cometogether.put()

        something = Song(
            artist=beatles.key(),
            album=abbeyroad.key(),
            duration="3:03",
            genre=db.Category("Pop"),
            title="Something")
        something.put()

        because1 = Song(
            key_name='because',
            artist=beatles.key(),
            album=abbeyroad.key(),
            duration="2:46",
            genre=db.Category("Pop"),
            title="Because")
        because1.put()

        because2= Song(
            artist=beatles.key(),
            album=abbeyroad.key(),
            duration="2:46",
            genre=db.Category("Pop"),
            title="Because")
        because2.put()

        query = (Song.all()
            .filter('artist =', beatles)
            .filter('album =', abbeyroad)
            .order('title'))

        self.assertEqual(
            [u'Because', u'Because', u'Come Together', u'Here Comes The Sun',
             u'Something'],
            [song.title for song in query.run()])

        query = Song.all().filter('title !=', 'Because').order('title')

        self.assertEqual(
            [u'Come Together', u'Here Comes The Sun', u'Something'],
            [song.title for song in query.run()])

        query = Song.all().filter('title >', 'Come').order('title')

        self.assertEqual(
            [u'Come Together', u'Here Comes The Sun', u'Something'],
            [song.title for song in query.run()])

        something.delete()

        query = Song.all().filter('title >', 'Come').order('title')

        self.assertEqual(
            [u'Come Together', u'Here Comes The Sun'],
            [song.title for song in query.run()])

    def testUnicode(self):
        """Tests unicode."""

        class Employee(db.Model):
            first_name = db.StringProperty(required=True)
            last_name = db.StringProperty(required=True)

        employee = Employee(first_name=u'Björn', last_name=u'Müller')
        employee.put()

        query = Employee.all(keys_only=True).filter('first_name =', u'Björn')
        self.assertEqual(
            datastore_types.Key.from_path(u'Employee', 1, _app=u'test'),
            query.get())

    def testListProperties(self):
        """Tests list properties."""

        class Numbers(db.Model):
            values = db.ListProperty(int)

        Numbers(values=[0, 1, 2, 3]).put()
        Numbers(values=[4, 5, 6, 7]).put()

        query = Numbers.all().filter('values =', 0)
        self.assertEqual([0, 1, 2, 3], query.get().values)

        query = db.GqlQuery(
            "SELECT * FROM Numbers WHERE values > :1 AND values < :2", 4, 7)
        self.assertEqual([4, 5, 6, 7], query.get().values)

        class Issue(db.Model):
            reviewers = db.ListProperty(db.Email)

        me = db.Email('me@somewhere.net')
        you = db.Email('you@home.net')
        issue = Issue(reviewers=[me, you])
        issue.put()

        query = db.GqlQuery(
            "SELECT * FROM Issue WHERE reviewers = :1",
            db.Email('me@somewhere.net'))

        self.assertEqual(1, query.count())

        query = db.GqlQuery(
            "SELECT * FROM Issue WHERE reviewers = :1",
            'me@somewhere.net')

        self.assertEqual(1, query.count())

        query = db.GqlQuery(
            "SELECT * FROM Issue WHERE reviewers = :1",
            db.Email('foo@bar.net'))

        self.assertEqual(0, query.count())

    def testStringListProperties(self):
        """Tests string list properties."""

        class Pizza(db.Model):
            topping = db.StringListProperty()

        Pizza(topping=["tomatoe", "cheese"]).put()
        Pizza(topping=["tomatoe", "cheese", "salami"]).put()
        Pizza(topping=["tomatoe", "cheese", "prosciutto"]).put()

        query = Pizza.all(keys_only=True).filter('topping =', "salami")
        self.assertEqual(1, query.count())

        query = Pizza.all(keys_only=True).filter('topping =', "cheese")
        self.assertEqual(3, query.count())

        query = Pizza.all().filter('topping IN', ["salami", "prosciutto"])
        self.assertEqual(2, query.count())

        key = datastore_types.Key.from_path('Pizza', 1)
        query = db.GqlQuery("SELECT * FROM Pizza WHERE __key__ IN :1", [key])
        pizza = query.get()
        self.assertEqual(["tomatoe", "cheese"], pizza.topping)

        pizza.delete()

        query = db.GqlQuery("SELECT * FROM Pizza WHERE __key__ IN :1", [key])
        self.assertEqual(0, query.count())

    def testVariousPropertiyTypes(self):
        """Tests various property types."""

        class Note(db.Model):
            timestamp = db.DateTimeProperty(auto_now=True)
            description = db.StringProperty()
            author_email = db.EmailProperty()
            location = db.GeoPtProperty()
            user = db.UserProperty()

        Note(
            description="My first note.",
            author_email="me@inter.net",
            location="52.518,13.408",
            user=google.appengine.api.users.get_current_user()
        ).put()

        query = db.GqlQuery("SELECT * FROM Note ORDER BY timestamp DESC")
        self.assertEqual(1, query.count())

        query = db.GqlQuery(
            "SELECT * FROM Note WHERE timestamp <= :1", datetime.datetime.now())

        self.assertEqual(1, query.count())

        note = query.get()

        self.assertEqual("My first note.", note.description)

        self.assertEqual(db.Email("me@inter.net"), note.author_email)
        self.assertEqual("me@inter.net", note.author_email)

        self.assertEqual(
            datastore_types.GeoPt(52.518000000000001, 13.407999999999999),
            note.location)
        self.assertEqual("52.518,13.408", note.location)

        del note

        query = Note.all().filter(
            'location =',
            datastore_types.GeoPt(52.518000000000001, 13.407999999999999))
        self.assertEqual(1, query.count())

        query = Note.all().filter('location =', "52.518,13.408")
        self.assertEqual(1, query.count())

    def testQueriesWithLimit(self):
        """Retrieves a limited number of results."""

        class MyModel(db.Model):
            property = db.StringProperty()

        for i in range(100):
            MyModel(property="Random data.").put()

        self.assertEqual(50, MyModel.all().count(limit=50))

    def testAllocateIds(self):
        """ """

        class EmptyModel(db.Model):
            pass

        for i in xrange(0, 1000):
            key = EmptyModel().put()

        query = db.GqlQuery("SELECT * FROM EmptyModel")
        self.assertEqual(1000, query.count())

        start, end = db.allocate_ids(key, 2000)
        self.assertEqual(start, 1000)
        self.assertEqual(end, 2999)

    def testCursors(self):
        """Tests the cursor API."""

        class Integer(db.Model):
            value = db.IntegerProperty()

        for i in xrange(0, 2000):
            Integer(value=i).put()

        # Set up a simple query.
        query = Integer.all()

        # Fetch some results.
        a = query.fetch(500)
        self.assertEqual(0L, a[0].value)
        self.assertEqual(499L, a[-1].value)

        b = query.fetch(500, offset=500)
        self.assertEqual(500L, b[0].value)
        self.assertEqual(999L, b[-1].value)

        # Perform several queries with a cursor.
        cursor = query.cursor()
        query.with_cursor(cursor)

        c = query.fetch(200)
        self.assertEqual(1000L, c[0].value)
        self.assertEqual(1199L, c[-1].value)

        query.with_cursor(query.cursor())
        d = query.fetch(500)
        self.assertEqual(1200L, d[0].value)
        self.assertEqual(1699L, d[-1].value)

        query.with_cursor(query.cursor())
        self.assertEqual(1700L, query.get().value)

        # Use a query with filters.
        query = Integer.all().filter('value >', 500).filter('value <=', 1000) 
        e = query.fetch(100)
        query.with_cursor(query.cursor())
        e = query.fetch(50)
        self.assertEqual(601, e[0].value)
        self.assertEqual(650, e[-1].value)

    def testGetSchema(self):
        """Infers an app's schema from the entities in the datastore."""

        class Foo(db.Model):
            foobar = db.IntegerProperty(default=42)

        Foo().put()

        entity_pbs = google.appengine.api.datastore_admin.GetSchema()
        entity = google.appengine.api.datastore.Entity.FromPb(entity_pbs.pop())
        self.assertEqual('Foo', entity.key().kind())
