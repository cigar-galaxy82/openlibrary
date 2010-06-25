"""Handlers for borrowing books"""

import datetime
import simplejson
import string
import urllib2

import web

from infogami import config
from infogami.utils import delegate
from infogami.utils.view import public

import utils
from utils import render_template

import acs4

########## Constants

lending_library_subject = 'Lending library'
loanstatus_url = config.loanstatus_url

content_server = None

########## Page Handlers

# Handler for /books/{bookid}/{title}/borrow
class borrow(delegate.page):
    path = "(/books/OL\d+M)/borrow"
    
    def GET(self, key):
        edition = web.ctx.site.get(key)
        
        if not edition:
            raise web.notfound()

        # XXX synchronize loan info from acs -- check for returns
            
        loans = []
        user = web.ctx.site.get_user()
        if user:
            # XXX synchronize user loans
            loans = get_loans(user)
            
        return render_template("borrow", edition, loans)
        
class do_borrow(delegate.page):
    """Actually borrow the book, via POST"""
    path = "(/books/OL\d+M)/_doborrow"
    
    def POST(self, key):
        i = web.input()
        
        user = web.ctx.site.get_user()
        
        if not user:
            raise web.seeother(key + '/borrow') # XXX not correct url.... need short title
        
        everythingChecksOut = True # XXX
        if everythingChecksOut:
            # XXX get loan URL and loan id            
            resourceType = 'epub'
            loan = Loan(user.key, key, resourceType)
            loan_link = loan.make_offer() # generate the link and record that loan offer occurred
            raise web.seeother(loan_link)
        else:
            # Send to the borrow page
            raise web.seeother(key + '/borrow') # XXX doesn't work because title is after OL id

########## Public Functions

@public
def overdrive_id(edition):
    identifier = None
    if edition.get('identifiers', None) and edition.identifiers.get('overdrive', None):
        identifier = edition.identifiers.overdrive[0]
    return identifier

@public
def can_borrow(edition):
    global lending_library_subject
    
    # Check if in overdrive
    # $$$ Should we also require to be in lending library?
    if overdrive_id(edition):
        return True
    
    # Check that work is in lending library
    inLendingLibrary = False
    for work in edition.get('works', []):
        subjects = work.get_subjects()
        if subjects:
            try:
                if subjects.index(lending_library_subject) >= 0:
                    inLendingLibrary = True
                    break
            except ValueError:
                pass
                
    if not inLendingLibrary:
        return False
    
    # Book is in lending library
    
    # Check if hosted at archive.org
    if edition.get('ocaid', False):
        return True
    
    return False

@public
def is_loan_available(edition, type):    
    resource_id = edition.get_lending_resource_id(type)
    
    if not resource_id:
        return False
        
    return not is_loaned_out(resource_id)

# XXX - currently here for development - put behind user limit and availability checks
@public
def get_loan_link(edition, type):
    global content_server
    
    if not content_server:
        if not config.content_server:
            # $$$ log
            return None
        content_server = ContentServer(config.content_server)
        
    resource_id = edition.get_lending_resource_id(type)
    return (resource_id, content_server.get_loan_link(resource_id))

########## Helper Functions

def get_loans(user):
    return [web.ctx.site.store[result['key']] for result in web.ctx.site.store.query('/type/loan', 'user', user.key)]

def is_loaned_out(resource_id):
    global loanstatus_url
    
    if not loanstatus_url:
        raise Exception('No loanstatus_url -- cannot check loan status')
    
    # BSS response looks like this:
    #
    # [
    #     {
    #         "loanuntil": "2010-06-25T00:52:04", 
    #         "resourceid": "a8b600e2-32fd-4aeb-a2b5-641103583254", 
    #         "returned": "F", 
    #         "until": "2010-06-25T00:52:04"
    #     }
    # ]

    url = '%s/is_loaned_out/%s' % (loanstatus_url, resource_id)
    try:
        response = simplejson.loads(urllib2.urlopen(url).read())
        if len(response) == 0:
            # No outstanding loans
            return False
        
        if response[0]['returned'] in ['F','?']:
            return True
            
        if response[0]['returned'] == 'T':
            # Current loan has been returned
            return False
            
    except IOError:
        # status server is down
        # XXX be more graceful
        raise Exception('Loan status server not available')
    
    raise Exception('Error communicating with loan status server for resource %s' % resource_id)

########## Classes

class Loan:

    default_loan_delta = datetime.timedelta(weeks = 2)
    iso_format = "%Y-%m-%dT%H:%M:%S.%f"

    def __init__(self, user_key, book_key, resource_type, expiry = None, loaned_at = None):
        self.user_key = user_key
        self.book_key = book_key
        self.resource_type = resource_type
        self.type = '/type/loan'
        self.resource_id = None
        self.offer_url = None
        
        if loaned_at is not None:
            self.loaned_at = loaned_at
        else:
            self.loaned_at = datetime.datetime.utcnow().isoformat()

        if expiry is not None:
            self.expiry = expiry
        else:
            # XXX set proper expiry
            self.expiry = datetime.datetime.strptime(self.loaned_at, Loan.iso_format)
        
    def get_key(self):
        return '%s-%s-%s' % (self.user_key, self.book_key, self.resource_type)
        
    def get_dict(self):
        return { 'user': self.user_key, 'type': '/type/loan',
                 'book': self.book_key, 'expiry': self.expiry,
                 'loaned_at': self.loaned_at, 'resource_type': self.resource_type,
                 'resource_id': self.resource_id, 'offer_url': self.offer_url }
                 
    def set_dict(self, loan_dict):
        self.user_key = loan_dict['user']
        self.type = loan_dict['type']
        self.book_key = loan_dict['book']
        self.resource_type = loan_dict['resource_type']
        self.expiry = loan_dict['expiry']
        self.loaned_at = loan_dict['loaned_at']
        self.resource_id = loan_dict['resource_id']
        self.offer_url = loan_dict['offer_url']
        
    def load(self):
        self.set_dict(web.ctx.site.store[self.get_key()])
        
    def save(self):
        web.ctx.site.store[self.get_key()] = self.get_dict()
        
    def remove(self):
        web.ctx.site.delete(self.get_key())
        
    def make_offer(self):
        """Create loan url and record that loan was offered.  Returns the link URL that triggers
           Digital Editions to open."""
        edition = web.ctx.site.get(self.book_key)
        resource_id, loan_link = get_loan_link(edition, self.resource_type)
        if not loan_link:
            raise Exception('Could not get loan link for edition %s type %s' % self.book_key, self.resource_type)
        self.offer_url = loan_link
        self.resource_id = resource_id
        self.save()
        return loan_link
        
class ContentServer:
    def __init__(self, config):
        self.host = config.host
        self.port = config.port
        self.password = config.password
        self.distributor = config.distributor
        
        # Contact server to get shared secret for signing
        result = acs4.get_distributor_info(self.host, self.password, self.distributor)
        self.shared_secret = result['sharedSecret']
        self.name = result['name']

    def get_loan_link(self, resource_id):
        loan_link = acs4.mint(self.host, self.shared_secret, resource_id, 'enterloan', self.name, port = self.port)
        return loan_link
