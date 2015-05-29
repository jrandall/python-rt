"""
======================================================
 Rt - Python interface to Request Tracker :term:`API`
======================================================

Description of Request Tracker :term:`REST` :term:`API`:
http://requesttracker.wikia.com/wiki/REST

Provided functionality:

* login to RT
* logout
* getting, creating and editing tickets
* getting attachments
* getting history of ticket
* replying to ticket requestors
* adding comments
* getting and editing ticket links
* searching
* providing lists of last updated tickets
* providing tickets with new correspondence
* merging tickets
* take tickets
* steal tickets
* untake tickets
"""

__license__ = """ Copyright (C) 2012 CZ.NIC, z.s.p.o.
    Copyright (c) 2015 Genome Research Ltd.

    This program is free software: you can redistribute it and/or modify
    it under the terms of the GNU General Public License as published by
    the Free Software Foundation, either version 3 of the License, or
    (at your option) any later version.

    This program is distributed in the hope that it will be useful,
    but WITHOUT ANY WARRANTY; without even the implied warranty of
    MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
    GNU General Public License for more details.

    You should have received a copy of the GNU General Public License
    along with this program.  If not, see <http://www.gnu.org/licenses/>.
"""
__docformat__ = "reStructuredText en"
__authors__ = [
  '"Jiri Machalek" <jiri.machalek@nic.cz>',
  '"Joshua C. randall" <jcrandall@alum.mit.edu>'
]

import re
import os
import requests
from requests.auth import HTTPBasicAuth, HTTPDigestAuth

from six import iteritems
from six.moves import range

DEFAULT_QUEUE = 'General'
""" Default queue used. """

ALL_QUEUES = object()

class RtError(Exception):
    """ Super class of all Rt Errors """

    pass

class AuthorizationError(RtError):
    """ Exception raised when module cannot access :term:`API` due to invalid
    or missing credentials. """

    pass

class NotAllowed(RtError):
    """ Exception raised when request cannot be finished due to
    insufficient privileges. """

    pass

class UnexpectedResponse(RtError):
    """ Exception raised when unexpected HTTP code is received. """

    pass

class UnexpectedMessageFormat(RtError):
    """ Exception raised when response has bad status code (not the HTTP code,
    but code in the first line of the body as 200 in `RT/4.0.7 200 Ok`)
    or message parsing fails because of unexpected format. """

    pass

class APISyntaxError(RtError):
    """ Exception raised when syntax error is received. """

    pass

class InvalidUse(RtError):
    """ Exception raised when API method is not used correctly. """

    pass

class BadRequest(RtError):
    """ Exception raised when HTTP code 400 (Bad Request) is received. """

    pass

class ConnectionError(RtError):
    """ Encapsulation of various exceptions indicating network problems. """

    def __init__(self, message, cause):
        """ Initialization of exception extented by cause parameter.
        
        :keyword message: Exception details
        :keyword cause: Cause exception
        """
        super(ConnectionError, self).__init__(message + ' (Caused by ' + repr(cause) + ")")
        self.cause = cause

class InvalidQueryError(RtError):
    """ Exception raised when attempting to search RT with an invalid raw query. """

    pass

class Rt:
    """ :term:`API` for Request Tracker according to
    http://requesttracker.wikia.com/wiki/REST. Interface is based on
    :term:`REST` architecture, which is based on HTTP/1.1 protocol. This module
    is therefore mainly sending and parsing special HTTP messages.
    
    .. note:: Use only ASCII LF as newline (``\\n``). Time is returned in UTC.
              All strings returned are encoded in UTF-8 and the same is
              expected as input for string values.
    """

    RE_PATTERNS = {
        'not_allowed_pattern': re.compile('^# You are not allowed to'),
        'credentials_required_pattern': re.compile('.* 401 Credentials required$'),
        'syntax_error_pattern': re.compile('.* 409 Syntax Error$'),
        'requestors_pattern': re.compile('Requestors:'),
        'update_pattern': re.compile('^# Ticket [0-9]+ updated.$'),
        'content_pattern': re.compile('Content:'),
        'content_pattern_bytes': re.compile(b'Content:'),
        'attachments_pattern': re.compile('Attachments:'),
        'attachments_list_pattern': re.compile(r'[^0-9]*(\d+): (.+) \((.+) / (.+)\),?$'),
        'headers_pattern_bytes': re.compile(b'Headers:'),
        'links_updated_pattern': re.compile('^# Links for ticket [0-9]+ updated.$'),
        'created_link_pattern': re.compile('.* Created link '),
        'deleted_link_pattern': re.compile('.* Deleted link '),
        'merge_successful_pattern': re.compile('^# Merge completed.|^Merge Successful$'),
        'bad_request_pattern': re.compile('.* 400 Bad Request$'),
        'user_pattern': re.compile('^# User ([0-9]*) (?:updated|created)\.$'),
        'queue_pattern': re.compile('^# Queue (\w*) (?:updated|created)\.$'),
        'does_not_exist_pattern': re.compile('^# (?:Queue|User|Ticket) \w* does not exist\.$'),
        'does_not_exist_pattern_bytes': re.compile(b'^# (?:Queue|User|Ticket) \w* does not exist\.$'),
        'not_related_pattern': re.compile('^# Transaction \d+ is not related to Ticket \d+'),
        'invalid_attachment_pattern_bytes': re.compile(b'^# Invalid attachment id: \d+$'),
    }

    def __init__(self, url, default_login=None, default_password=None, proxy=None,
                 default_queue=DEFAULT_QUEUE, basic_auth=None, digest_auth=None,
                 skip_login=False):
        """ API initialization.
        
        :keyword url: Base URL for Request Tracker API.
                      E.g.: http://tracker.example.com/REST/1.0/
        :keyword default_login: Default RT login used by self.login if no
                                other credentials are provided
        :keyword default_password: Default RT password
        :keyword proxy: Proxy server (string with http://user:password@host/ syntax)
        :keyword default_queue: Default RT queue
        :keyword basic_auth: HTTP Basic authentication credentials, tuple (UN, PW)
        :keyword digest_auth: HTTP Digest authentication credentials, tuple (UN, PW)
        :keyword skip_login: Set this option True when HTTP Basic authentication
                             credentials for RT are in .netrc file. You do not
                             need to call login, because it is managed by
                             requests library instantly.
        """
        self.url = url
        self.default_login = default_login
        self.default_password = default_password
        self.default_queue = default_queue
        self.login_result = None
        self.session = requests.session()
        if proxy is not None:
            if url.lower().startswith("https://"):
                proxy = {"https": proxy}
            else:
                proxy = {"http": proxy}
        self.session.proxies = proxy
        if basic_auth:
            self.session.auth = HTTPBasicAuth(*basic_auth)
        if digest_auth:
            self.session.auth = HTTPDigestAuth(*digest_auth)
        if basic_auth or digest_auth or skip_login:
            # Assume valid credentials, because we do not need to call login()
            # explicitly with basic or digest authentication (or if this is
            # assured, that we are login in instantly)
            self.login_result = True

    def __request(self, selector, get_params={}, post_data={}, files=[], without_login=False,
                  text_response=True):
        """ General request for :term:`API`.
 
        :keyword selector: End part of URL which completes self.url parameter
                           set during class inicialization.
                           E.g.: ``ticket/123456/show``
        :keyword post_data: Dictionary with POST method fields
        :keyword files: List of pairs (filename, file-like object) describing
                        files to attach as multipart/form-data
                        (list is necessary to keep files ordered)
        :keyword without_login: Turns off checking last login result
                                (usually needed just for login itself)
        :keyword text_response: If set to false the received message will be
                                returned without decoding (useful for attachments)
        :returns: Requested messsage including state line in form
                  ``RT/3.8.7 200 Ok\\n``
        :rtype: string or bytes if text_response is False
        :raises AuthorizationError: In case that request is called without previous
                                    login or login attempt failed.
        :raises ConnectionError: In case of connection error.
        """
        try:
            if (not self.login_result) and (not without_login):
                raise AuthorizationError('First login by calling method `login`.')
            url = str(os.path.join(self.url, selector))
            if not files:
                if post_data:
                    response = self.session.post(url, data=post_data)
                else:
                    response = self.session.get(url, params=get_params)
            else:
                files_data = {}
                for i, file_pair in enumerate(files):
                    files_data['attachment_%d' % (i+1)] = file_pair
                response = self.session.post(url, data=post_data, files=files_data)
            if response.status_code == 401:
                raise AuthorizationError('Server could not verify that you are authorized to access the requested document.')
            if response.status_code != 200:
                raise UnexpectedResponse('Received status code %d instead of 200.' % response.status_code)
            try:
                if response.encoding:
                    result = response.content.decode(response.encoding.lower())
                else:
                    # try utf-8 if encoding is not filled
                    result = response.content.decode('utf-8')
            except LookupError:
                raise UnexpectedResponse('Unknown response encoding: %s.' % response.encoding)
            except UnicodeError:
                if text_response:
                    raise UnexpectedResponse('Unknown response encoding (UTF-8 does not work).')
                else:
                    # replace errors - we need decoded content just to check for error codes in __check_response
                    result = response.content.decode('utf-8', 'replace')
            self.__check_response(result)
            if not text_response:
                return response.content
            return result
        except requests.exceptions.ConnectionError as e:
            raise ConnectionError("Connection error", e)

    def __get_status_code(self, msg):
        """ Select status code given message.

        :keyword msg: Result message
        :returns: Status code
        :rtype: int
        """
        try:
            return int(msg.split('\n')[0].split(' ')[1])
        except:
            return None

    def __check_response(self, msg):
        """ Search general errors in server response and raise exceptions when found.

        :keyword msg: Result message
        :raises NotAllowed: Exception raised when operation was called with
                            insufficient privileges
        :raises AuthorizationError: Credentials are invalid or missing
        :raises APISyntaxError: Syntax error
        """
        if not isinstance(msg, list):
            msg = msg.split("\n")
        if (len(msg) > 2) and self.RE_PATTERNS['not_allowed_pattern'].match(msg[2]):
            raise NotAllowed(msg[2][2:])
        if self.RE_PATTERNS['credentials_required_pattern'].match(msg[0]):
            raise AuthorizationError('Credentials required.')
        if self.RE_PATTERNS['syntax_error_pattern'].match(msg[0]):
            raise APISyntaxError(msg[2][2:] if len(msg) > 2 else 'Syntax error.')
        if self.RE_PATTERNS['bad_request_pattern'].match(msg[0]):
            raise BadRequest(msg[2][2:] if len(msg) > 2 else 'Bad request.')

    def __normalize_list(self, msg):
        """Split message to list by commas and trim whitespace."""
        if isinstance(msg, list):
            msg = "".join(msg)
        return list(map(lambda x: x.strip(), msg.split(",")))

    def login(self, login=None, password=None):
        """ Login with default or supplied credetials.
        
        .. note::
            
            Calling this method is not necessary when HTTP basic or HTTP
            digest_auth authentication is used and RT accepts it as external
            authentication method, because the login in this case is done
            transparently by requests module. Anyway this method can be useful
            to check whether given credentials are valid or not.
        
        :keyword login: Username used for RT, if not supplied together with
                        *password* :py:attr:`~Rt.default_login` and
                        :py:attr:`~Rt.default_password` are used instead
        :keyword password: Similarly as *login*
        
        :returns: ``True``
                      Successful login
                  ``False``
                      Otherwise
        :raises AuthorizationError: In case that credentials are not supplied neither
                                    during inicialization or call of this method.
        """

        if (login is not None) and (password is not None):
            login_data = {'user':login, 'pass':password}
        elif (self.default_login is not None) and (self.default_password is not None):
            login_data = {'user':self.default_login, 'pass':self.default_password}
        elif self.session.auth:
            login_data = None
        else:
            raise AuthorizationError('Credentials required, fill login and password.')
        try:
            self.login_result = self.__get_status_code(self.__request('',
                                                                      post_data=login_data,
                                                                      without_login=True)) == 200
        except AuthorizationError:
            # This happens when HTTP Basic or Digest authentication fails, but
            # we will not raise the error but just return False to indicate
            # invalid credentials
            return False
        return self.login_result

    def logout(self):
        """ Logout of user.
        
        :returns: ``True``
                      Successful logout
                  ``False``
                      Logout failed (mainly because user was not login)
        """
        ret = False
        if self.login_result == True:
            ret = self.__get_status_code(self.__request('logout')) == 200
            self.login_result = None
        return ret
        
    def new_correspondence(self, queue=None):
        """ Obtains tickets changed by other users than the system one.
        
        :keyword queue: Queue where to search
        
        :returns: List of tickets which were last updated by other user than
                  the system one ordered in decreasing order by LastUpdated.
                  Each ticket is dictionary, the same as in
                  :py:meth:`~Rt.get_ticket`.
        """
        return self.search(Queue=queue, order='-LastUpdated', LastUpdatedBy__notexact=self.default_login)
        
    def last_updated(self, since, queue=None):
        """ Obtains tickets changed after given date.
        
        :param since: Date as string in form '2011-02-24'
        :keyword queue: Queue where to search
        
        :returns: List of tickets with LastUpdated parameter later than
                  *since* ordered in decreasing order by LastUpdated.
                  Each tickets is dictionary, the same as in
                  :py:meth:`~Rt.get_ticket`.
        """
        return self.search(Queue=queue, order='-LastUpdated', LastUpdatedBy__notexact=self.default_login, LastUpdated__gt=since)

    def search(self, Queue=None, order=None, raw_query=None, **kwargs):
        """ Search arbitrary needles in given fields and queue.
        
        Example::
            
            >>> tracker = Rt('http://tracker.example.com/REST/1.0/', 'rt-username', 'top-secret')
            >>> tracker.login()
            >>> tickets = tracker.search(CF_Domain='example.com', Subject__like='warning')
            >>> tickets = tracker.search(Queue='General', order='Status', raw_query="id='1'+OR+id='2'+OR+id='3'")

        :keyword Queue:      Queue where to search. If you wish to search across
                             all of your queues, pass the ALL_QUEUES object as the
                             argument.
        :keyword order:      Name of field sorting result list, for descending
                             order put - before the field name. E.g. -Created
                             will put the newest tickets at the beginning
        :keyword raw_query:  A raw query to provide to RT if you know what
                             you are doing. You may still pass Queue and order 
                             kwargs, so use these instead of including them in 
                             the raw query. You can refer to the RT query builder.
                             If passing raw_query, all other **kwargs will be ignored.
        :keyword kwargs:     Other arguments possible to set if not passing raw_query:
                         
                             Requestors, Subject, Cc, AdminCc, Owner, Status,
                             Priority, InitialPriority, FinalPriority,
                             TimeEstimated, Starts, Due, Text,... (according to RT
                             fields)

                             Custom fields CF.{<CustomFieldName>} could be set
                             with keywords CF_CustomFieldName.

                             To alter lookup operators you can append one of the
                             following endings to each keyword:

                             __exact    for operator = (default)
                             __notexact for operator !=
                             __gt       for operator >
                             __lt       for operator <
                             __like     for operator LIKE
                             __notlike  for operator NOT LIKE
            
                             Setting values to keywords constrain search
                             result to the tickets satisfying all of them.

        :returns: List of matching tickets. Each ticket is the same dictionary
                  as in :py:meth:`~Rt.get_ticket`.
        :raises:  UnexpectedMessageFormat: Unexpected format of returned message.
                  InvalidQueryError: If raw query is malformed
        """
        get_params = {}
        query = []
        url = 'search/ticket'
        if Queue is not ALL_QUEUES:
            query.append("Queue=\'%s\'" % (Queue or self.default_queue))
        if not raw_query:
            operators_map = {
                'gt':'>',
                'lt':'<',
                'exact':'=',
                'notexact':'!=',
                'like':' LIKE ',
                'notlike':' NOT LIKE '
                }

            for key, value in iteritems(kwargs):
                op = '='
                key_parts = key.split('__')
                if len(key_parts) > 1:
                    key = '__'.join(key_parts[:-1])
                    op = operators_map.get(key_parts[-1], '=')
                if key[:3] != 'CF_':
                    query.append("%s%s\'%s\'" % (key, op, value))
                else:
                    query.append("CF.{%s}%s\'%s\'" % (key[3:], op, value))
        else:
            query.append(raw_query)
        get_params['query'] = ' AND '.join('(' + part + ')' for part in query)
        if order:
            get_params['orderby'] = order
        get_params['format'] = 'l'

        msg = self.__request(url, get_params=get_params)
        lines = msg.split('\n')
        if len(lines) > 2:
            if self.__get_status_code(lines[0]) != 200 and lines[2].startswith('Invalid query: '):
                raise InvalidQueryError(lines[2])
            if lines[2].startswith('No matching results.'):
                return []

        msgs = map(lambda x: x.split('\n'), msg.split('\n--\n'))
        items = []
        for msg in msgs:
            pairs = {}
            req_matching = [i for i, m in enumerate(msg) if self.RE_PATTERNS['requestors_pattern'].match(m)]
            req_id = req_matching[0] if req_matching else None
            if not req_id:
                raise UnexpectedMessageFormat('Missing line starting with `Requestors:`.')
            for i in range(req_id):
                if ': ' in msg[i]:
                    header, content = msg[i].split(': ', 1)
                    pairs[header.strip()] = content.strip()
            requestors = [msg[req_id][12:]]
            req_id += 1
            while (req_id < len(msg)) and (msg[req_id][:12] == ' ' * 12):
                requestors.append(msg[req_id][12:])
                req_id += 1
            pairs['Requestors'] = self.__normalize_list(requestors)
            for i in range(req_id, len(msg)):
                if ': ' in msg[i]:
                    header, content = msg[i].split(': ', 1)
                    pairs[header.strip()] = content.strip()
            if pairs:
                items.append(pairs)    
        return items

    def get_ticket(self, ticket_id):
        """ Fetch ticket by its ID.
        
        :param ticket_id: ID of demanded ticket
        
        :returns: Dictionary with key, value pairs for ticket with
                  *ticket_id* or None if ticket does not exist. List of keys:
                  
                      * id
                      * Queue
                      * Owner
                      * Creator
                      * Subject
                      * Status
                      * Priority
                      * InitialPriority
                      * FinalPriority
                      * Requestors
                      * Cc
                      * AdminCc
                      * Created
                      * Starts
                      * Started
                      * Due
                      * Resolved
                      * Told
                      * TimeEstimated
                      * TimeWorked
                      * TimeLeft
        :raises UnexpectedMessageFormat: Unexpected format of returned message.
        """
        msg = self.__request('ticket/%s/show' % (str(ticket_id),))
        status_code = self.__get_status_code(msg)
        if status_code == 200:
            pairs = {}
            msg = msg.split('\n')
            if (len(msg) > 2) and self.RE_PATTERNS['does_not_exist_pattern'].match(msg[2]):
                return None
            req_matching = [i for i, m in enumerate(msg) if self.RE_PATTERNS['requestors_pattern'].match(m)]
            req_id = req_matching[0] if req_matching else None
            if not req_id:
                raise UnexpectedMessageFormat('Missing line starting with `Requestors:`.')
            for i in range(req_id):
                if ': ' in msg[i]:
                    header, content = msg[i].split(': ', 1)
                    pairs[header.strip()] = content.strip()
            requestors = [msg[req_id][12:]]
            req_id += 1
            while (req_id < len(msg)) and (msg[req_id][:12] == ' ' * 12):
                requestors.append(msg[req_id][12:])
                req_id += 1
            pairs['Requestors'] = self.__normalize_list(requestors)
            for i in range(req_id, len(msg)):
                if ': ' in msg[i]:
                    header, content = msg[i].split(': ', 1)
                    pairs[header.strip()] = content.strip()
            return pairs
        else:
            raise UnexpectedMessageFormat('Received status code is %d instead of 200.' % status_code)

    def create_ticket(self, Queue=None, **kwargs):
        """ Create new ticket and set given parameters.
        
        Example of message sended to ``http://tracker.example.com/REST/1.0/ticket/new``::

            content=id: ticket/new
            Queue: General
            Owner: Nobody
            Requestors: somebody@example.com
            Subject: Ticket created through REST API
            Text: Lorem Ipsum
    
        In case of success returned message has this form::

            RT/3.8.7 200 Ok
    
            # Ticket 123456 created.
            # Ticket 123456 updated.
    
        Otherwise::

            RT/3.8.7 200 Ok

            # Required: id, Queue
    
        + list of some key, value pairs, probably default values.
        
        :keyword Queue: Queue where to create ticket
        :keyword kwargs: Other arguments possible to set:
                         
                         Requestors, Subject, Cc, AdminCc, Owner, Status,
                         Priority, InitialPriority, FinalPriority,
                         TimeEstimated, Starts, Due, Text,... (according to RT
                         fields)

                         Custom fields CF.{<CustomFieldName>} could be set
                         with keywords CF_CustomFieldName.
        :returns: ID of new ticket or ``-1``, if creating failed
        """

        post_data = 'id: ticket/new\nQueue: %s\n' % (Queue or self.default_queue,)
        for key in kwargs:
            if key[:3] != 'CF_':
                post_data += "%s: %s\n"%(key, kwargs[key])
            else:
                post_data += "CF.{%s}: %s\n"%(key[3:], kwargs[key])
        msg = self.__request('ticket/new', post_data={'content':post_data})
        state = msg.split('\n')[2]
        res = re.search(' [0-9]+ ',state)
        if res is not None:
            return int(state[res.start():res.end()])
        else:
            return -1

    def edit_ticket(self, ticket_id, **kwargs):
        """ Edit ticket values.
    
        :param ticket_id: ID of ticket to edit
        :keyword kwargs: Other arguments possible to set:
                         
                         Requestors, Subject, Cc, AdminCc, Owner, Status,
                         Priority, InitialPriority, FinalPriority,
                         TimeEstimated, Starts, Due, Text,... (according to RT
                         fields)

                         Custom fields CF.{<CustomFieldName>} could be set
                         with keywords CF_CustomFieldName.
        :returns: ``True``
                      Operation was successful
                  ``False``
                      Ticket with given ID does not exist or unknown parameter
                      was set (in this case all other valid fields are changed)
        """
        post_data = ''
        for key, value in iteritems(kwargs):
            if isinstance(value, (list, tuple)):
                value = ", ".join(value)
            if key[:3] != 'CF_':
                post_data += "%s: %s\n"%(key, value)
            else:
                post_data += "CF.{%s}: %s\n" % (key[3:], value)
        msg = self.__request('ticket/%s/edit' % (str(ticket_id)), post_data={'content':post_data})
        state = msg.split('\n')[2]
        return self.RE_PATTERNS['update_pattern'].match(state) is not None

    def get_history(self, ticket_id, transaction_id=None):
        """ Get set of history items.
        
        :param ticket_id: ID of ticket
        :keyword transaction_id: If set to None, all history items are
                                 returned, if set to ID of valid transaction
                                 just one history item is returned
                          
        :returns: List of history items ordered increasingly by time of event.
                  Each history item is dictionary with following keys:

                  Description, Creator, Data, Created, TimeTaken, NewValue,
                  Content, Field, OldValue, Ticket, Type, id, Attachments

                  All these fields are strings, just 'Attachments' holds list
                  of pairs (attachment_id,filename_with_size).

                  Returns None if ticket or transaction does not exist.
        :raises UnexpectedMessageFormat: Unexpected format of returned message.
        """
        if transaction_id is None:
            # We are using "long" format to get all history items at once.
            # Each history item is then separated by double dash.
            msgs = self.__request('ticket/%s/history?format=l' % (str(ticket_id),))
        else:
            msgs = self.__request('ticket/%s/history/id/%s' % (str(ticket_id), str(transaction_id)))
        lines = msgs.split('\n')
        if (len(lines) > 2) and \
               (self.RE_PATTERNS['does_not_exist_pattern'].match(lines[2]) or \
                self.RE_PATTERNS['not_related_pattern'].match(lines[2])):
            return None
        msgs = msgs.split('\n--\n')
        items = []
        for msg in msgs:
            pairs = {}
            msg = msg.split('\n')
            cont_matching = [i for i, m in enumerate(msg) if self.RE_PATTERNS['content_pattern'].match(m)]
            cont_id = cont_matching[0] if cont_matching else None
            if not cont_id:
                raise UnexpectedMessageFormat('Unexpected history entry. \
                                               Missing line starting with `Content:`.')
            atta_matching = [i for i, m in enumerate(msg) if self.RE_PATTERNS['attachments_pattern'].match(m)]
            atta_id = atta_matching[0] if atta_matching else None
            if not atta_id:
                raise UnexpectedMessageFormat('Unexpected attachment part of history entry. \
                                               Missing line starting with `Attachements:`.')
            for i in range(cont_id):
                if ': ' in msg[i]:
                    header, content = msg[i].split(': ', 1)
                    pairs[header.strip()] = content.strip()
            content = msg[cont_id][9:]
            cont_id += 1
            while (cont_id < len(msg)) and (msg[cont_id][:9] == ' ' * 9):
                content += '\n' + msg[cont_id][9:]
                cont_id += 1
            pairs['Content'] = content
            for i in range(cont_id, atta_id):
                if ': ' in msg[i]:
                    header, content = msg[i].split(': ', 1)
                    pairs[header.strip()] = content.strip()
            attachments = []
            for i in range(atta_id + 1, len(msg)):
                if ': ' in msg[i]:
                    header, content = msg[i].split(': ', 1)
                    attachments.append((int(header),
                                        content.strip()))
            pairs['Attachments'] = attachments
            items.append(pairs)    
        return items

    def get_short_history(self, ticket_id):
        """ Get set of short history items
        
        :param ticket_id: ID of ticket
        :returns: List of history items ordered increasingly by time of event.
                  Each history item is a tuple containing (id, Description).
                  Returns None if ticket does not exist.
        """
        msg = self.__request('ticket/%s/history' % (str(ticket_id),))
        items = []
        lines = msg.split('\n')
        if self.__get_status_code(lines[0]) == 200:
            if (len(lines) > 2) and self.RE_PATTERNS['does_not_exist_pattern'].match(lines[2]):
                return None
            if len(lines) >= 4:
                for line in lines[4:]:
                    if ': ' in line:
                        hist_id, desc = line.split(': ', 1)
                        items.append((int(hist_id), desc))
        return items
    
    def reply(self, ticket_id, text='', cc='', bcc='', files=[]):
        """ Sends email message to the contacts in ``Requestors`` field of
        given ticket with subject as is set in ``Subject`` field.

        Form of message according to documentation::

            id: <ticket-id>
            Action: correspond
            Text: the text comment
                  second line starts with the same indentation as first
            Cc: <...>
            Bcc: <...>
            TimeWorked: <...>
            Attachment: an attachment filename/path

        :param ticket_id: ID of ticket to which message belongs
        :keyword text: Content of email message
        :keyword cc: Carbon copy just for this reply
        :keyword bcc: Blind carbon copy just for this reply
        :keyword files: Files to attach as multipart/form-data
                        List of 2/3 tuples: (filename, file-like object, [content type])
        :returns: ``True``
                      Operation was successful
                  ``False``
                      Sending failed (status code != 200)
        :raises BadRequest: When ticket does not exist
        """
        post_data = {'content':"""id: %s
Action: correspond
Text: %s
Cc: %s
Bcc: %s"""%(str(ticket_id), re.sub(r'\n', r'\n      ', text), cc, bcc)}
        for file_info in files:
            post_data['content'] += "\nAttachment: %s" % (file_info[0],)
        msg = self.__request('ticket/%s/comment' % (str(ticket_id),),
                             post_data=post_data, files=files)
        return self.__get_status_code(msg) == 200

    def comment(self, ticket_id, text='', cc='', bcc='', files=[]):
        """ Adds comment to the given ticket.
        
        Form of message according to documentation::

            id: <ticket-id>
            Action: comment
            Text: the text comment
                  second line starts with the same indentation as first
            Attachment: an attachment filename/path

        Example::
            
            >>> tracker = Rt('http://tracker.example.com/REST/1.0/', 'rt-username', 'top-secret')
            >>> attachment_name = sys.argv[1]
            >>> message_text = ' '.join(sys.argv[2:])
            >>> ret = tracker.comment(ticket_id, text=message_text,
            ... files=[(attachment_name, open(attachment_name, 'rb'))])
            >>> if not ret:
            ...     print('Error: could not send attachment', file=sys.stderr)
            ...     exit(1)

        :param ticket_id: ID of ticket to which comment belongs
        :keyword text: Content of comment
        :keyword files: Files to attach as multipart/form-data
                        List of 2/3 tuples: (filename, file-like object, [content type])
        :returns: ``True``
                      Operation was successful
                  ``False``
                      Sending failed (status code != 200)
        :raises BadRequest: When ticket does not exist
        """
        post_data = {'content':"""id: %s
Action: comment
Text: %s""" % (str(ticket_id), re.sub(r'\n', r'\n      ', text))}
        for file_info in files:
            post_data['content'] += "\nAttachment: %s" % (file_info[0],)
        msg = self.__request('ticket/%s/comment' % (str(ticket_id),),
                             post_data=post_data, files=files)
        return self.__get_status_code(msg) == 200


    def get_attachments(self, ticket_id):
        """ Get attachment list for a given ticket
        
        :param ticket_id: ID of ticket
        :returns: List of tuples for attachments belonging to given ticket.
                  Tuple format: (id, name, content_type, size)
                  Returns None if ticket does not exist.
        """
        msg = self.__request('ticket/%s/attachments' % (str(ticket_id),))
        lines = msg.split('\n')
        if (len(lines) > 2) and self.RE_PATTERNS['does_not_exist_pattern'].match(lines[2]):
            return None
        attachment_infos = []
        if (self.__get_status_code(lines[0]) == 200) and (len(lines) >= 4):
            for line in lines[4:]:
                info = self.RE_PATTERNS['attachments_list_pattern'].match(line)
                if info:
                    attachment_infos.append(info.groups())
        return attachment_infos

    def get_attachments_ids(self, ticket_id):
        """ Get IDs of attachments for given ticket.
        
        :param ticket_id: ID of ticket
        :returns: List of IDs (type int) of attachments belonging to given
                  ticket. Returns None if ticket does not exist.
        """
        attachments = self.get_attachments(ticket_id)
        return [int(at[0]) for at in attachments] if attachments else attachments
        
    def get_attachment(self, ticket_id, attachment_id):
        """ Get attachment.
        
        :param ticket_id: ID of ticket
        :param attachment_id: ID of attachment for obtain
        :returns: Attachment as dictionary with these keys:

                      * Transaction
                      * ContentType
                      * Parent
                      * Creator
                      * Created
                      * Filename
                      * Content (bytes type)
                      * Headers
                      * MessageId
                      * ContentEncoding
                      * id
                      * Subject

                  All these fields are strings, just 'Headers' holds another
                  dictionary with attachment headers as strings e.g.:
                  
                      * Delivered-To
                      * From
                      * Return-Path
                      * Content-Length
                      * To
                      * X-Seznam-User
                      * X-QM-Mark
                      * Domainkey-Signature
                      * RT-Message-ID
                      * X-RT-Incoming-Encryption
                      * X-Original-To
                      * Message-ID
                      * X-Spam-Status
                      * In-Reply-To
                      * Date
                      * Received
                      * X-Country
                      * X-Spam-Checker-Version
                      * X-Abuse
                      * MIME-Version
                      * Content-Type
                      * Subject

                  .. warning:: Content-Length parameter is set after opening
                               ticket in web interface!

                  Set of headers available depends on mailservers sending
                  emails not on Request Tracker!

                  Returns None if ticket or attachment does not exist.
        :raises UnexpectedMessageFormat: Unexpected format of returned message.
        """
        msg = self.__request('ticket/%s/attachments/%s' % (str(ticket_id), str(attachment_id)),
                             text_response=False)
        msg = msg.split(b'\n')
        if (len(msg) > 2) and \
               (self.RE_PATTERNS['invalid_attachment_pattern_bytes'].match(msg[2]) or \
                self.RE_PATTERNS['does_not_exist_pattern_bytes'].match(msg[2])):
            return None
        msg = msg[2:]
        head_matching = [i for i, m in enumerate(msg) if self.RE_PATTERNS['headers_pattern_bytes'].match(m)]
        head_id = head_matching[0] if head_matching else None
        if not head_id:
            raise UnexpectedMessageFormat('Unexpected headers part of attachment entry. \
                                           Missing line starting with `Headers:`.')
        msg[head_id] = re.sub(b'^Headers: (.*)$', r'\1', msg[head_id])
        cont_matching = [i for i, m in enumerate(msg) if self.RE_PATTERNS['content_pattern_bytes'].match(m)]
        cont_id = cont_matching[0] if cont_matching else None
        if not cont_matching:
            raise UnexpectedMessageFormat('Unexpected content part of attachment entry. \
                                           Missing line starting with `Content:`.')
        pairs = {}
        for i in range(head_id):
            if b': ' in msg[i]:
                header, content = msg[i].split(b': ', 1)
                pairs[header.strip().decode('utf-8')] = content.strip().decode('utf-8')
        headers = {}
        for i in range(head_id, cont_id):
            if b': ' in msg[i]:
                header, content = msg[i].split(b': ', 1)
                headers[header.strip().decode('utf-8')] = content.strip().decode('utf-8')
        pairs['Headers'] = headers
        content = msg[cont_id][9:]
        for i in range(cont_id + 1, len(msg)):
            if msg[i][:9] == (b' ' * 9):
                content += b'\n' + msg[i][9:]
        pairs['Content'] = content
        return pairs

    def get_attachment_content(self, ticket_id, attachment_id):
        """ Get content of attachment without headers.

        This function is necessary to use for binary attachment,
        as it can contain ``\\n`` chars, which would disrupt parsing
        of message if :py:meth:`~Rt.get_attachment` is used.
        
        Format of message::

            RT/3.8.7 200 Ok\n\nStart of the content...End of the content\n\n\n
        
        :param ticket_id: ID of ticket
        :param attachment_id: ID of attachment
        
        Returns: Bytes with content of attachment or None if ticket or
                 attachment does not exist.
        """
    
        msg = self.__request('ticket/%s/attachments/%s/content' %
                             (str(ticket_id), str(attachment_id)),
                             text_response=False)
        lines = msg.split(b'\n', 3)
        if (len(lines) == 4) and \
               (self.RE_PATTERNS['invalid_attachment_pattern_bytes'].match(lines[2]) or \
                self.RE_PATTERNS['does_not_exist_pattern_bytes'].match(lines[2])):
            return None
        return msg[msg.find(b'\n') + 2:-3]

    def get_user(self, user_id):
        """ Get user details.
        
        :param user_id: Identification of user by username (str) or user ID
                        (int)
        :returns: User details as strings in dictionary with these keys for RT
                  users:

                      * Lang
                      * RealName
                      * Privileged
                      * Disabled
                      * Gecos
                      * EmailAddress
                      * Password
                      * id
                      * Name

                  Or these keys for external users (e.g. Requestors replying
                  to email from RT:

                      * RealName
                      * Disabled
                      * EmailAddress
                      * Password
                      * id
                      * Name

                  None is returned if user does not exist.
        :raises UnexpectedMessageFormat: In case that returned status code is not 200
        """
        msg = self.__request('user/%s' % (str(user_id),))
        status_code = self.__get_status_code(msg)
        if(status_code == 200):
            pairs = {}
            lines = msg.split('\n')
            if (len(lines) > 2) and self.RE_PATTERNS['does_not_exist_pattern'].match(lines[2]):
                return None
            for line in lines[2:]:
                if ': ' in line:
                    header, content = line.split(': ', 1)
                    pairs[header.strip()] = content.strip()
            return pairs
        else:
            raise UnexpectedMessageFormat('Received status code is %d instead of 200.' % status_code)


    def create_user(self, Name, EmailAddress, **kwargs):
        """ Create user (undocumented API feature).
        
        :param Name: User name (login for privileged, required)
        :param EmailAddress: Email address (required)
        :param kwargs: Optional fields to set (see edit_user)
        :returns: ID of new user or False when create fails
        :raises BadRequest: When user already exists
        :raises InvalidUse: When invalid fields are set
        """

        return self.edit_user('new', Name=Name, EmailAddress=EmailAddress, **kwargs)


    def edit_user(self, user_id, **kwargs):
        """ Edit user profile (undocumented API feature).

        :param user_id: Identification of user by username (str) or user ID
                        (int)
        :param kwargs: Other fields to edit from the following list:

                          * Name
                          * Password
                          * EmailAddress
                          * RealName
                          * NickName
                          * Gecos
                          * Organization
                          * Address1
                          * Address2
                          * City
                          * State
                          * Zip
                          * Country
                          * HomePhone
                          * WorkPhone
                          * MobilePhone
                          * PagerPhone
                          * ContactInfo
                          * Comments
                          * Signature
                          * Lang
                          * EmailEncoding
                          * WebEncoding
                          * ExternalContactInfoId
                          * ContactInfoSystem
                          * ExternalAuthId
                          * AuthSystem
                          * Privileged
                          * Disabled

        :returns: ID of edited user or False when edit fails
        :raises BadRequest: When user does not exist
        :raises InvalidUse: When invalid fields are set
        """

        valid_fields = set(('name', 'password', 'emailaddress', 'realname',
            'nickname', 'gecos', 'organization', 'address1', 'address2',
            'city', 'state', 'zip', 'country', 'homephone', 'workphone',
            'mobilephone', 'pagerphone', 'contactinfo', 'comments',
            'signature', 'lang', 'emailencoding', 'webencoding',
            'externalcontactinfoid', 'contactinfosystem', 'externalauthid',
            'authsystem', 'privileged', 'disabled'))
        used_fields = set(map(lambda x: x.lower(), kwargs.keys()))

        if not used_fields <= valid_fields:
            invalid_fields = ", ".join(list(used_fields - valid_fields))
            raise InvalidUse("Unsupported names of fields: %s." % invalid_fields)
        post_data = 'id: user/%s\n' % str(user_id)
        for key, val in iteritems(kwargs):
            post_data += '%s: %s\n' % (key, val)
        msg = self.__request('edit', post_data={'content': post_data})
        msgs = msg.split('\n')
        if (self.__get_status_code(msg) == 200) and (len(msgs) > 2):
            match = self.RE_PATTERNS['user_pattern'].match(msgs[2])
            if match:
                return int(match.group(1))
        return False
                        

    def get_queue(self, queue_id):
        """ Get queue details.
        
        :param queue_id: Identification of queue by name (str) or queue ID
                         (int)
        :returns: Queue details as strings in dictionary with these keys
                  if queue exists (otherwise None):

                      * id
                      * Name
                      * Description
                      * CorrespondAddress
                      * CommentAddress
                      * InitialPriority
                      * FinalPriority
                      * DefaultDueIn

        :raises UnexpectedMessageFormat: In case that returned status code is not 200
        """
        msg = self.__request('queue/%s' % str(queue_id))
        status_code = self.__get_status_code(msg)
        if(status_code == 200):
            pairs = {}
            lines = msg.split('\n')
            if (len(lines) > 2) and self.RE_PATTERNS['does_not_exist_pattern'].match(lines[2]):
                return None
            for line in lines[2:]:
                if ': ' in line:
                    header, content = line.split(': ', 1)
                    pairs[header.strip()] = content.strip()
            return pairs
        else:
            raise UnexpectedMessageFormat('Received status code is %d instead of 200.' % status_code)


    def edit_queue(self, queue_id, **kwargs):
        """ Edit queue (undocumented API feature).

        :param queue_id: Identification of queue by name (str) or ID (int)
        :param kwargs: Other fields to edit from the following list:

                          * Name
                          * Description
                          * CorrespondAddress
                          * CommentAddress
                          * InitialPriority
                          * FinalPriority
                          * DefaultDueIn

        :returns: ID or name of edited queue or False when edit fails
        :raises BadRequest: When queue does not exist
        :raises InvalidUse: When invalid fields are set
        """

        valid_fields = set(('name', 'description', 'correspondaddress', 'commentaddress', 'initialpriority', 'finalpriority', 'defaultduein'))
        used_fields = set(map(lambda x: x.lower(), kwargs.keys()))

        if not used_fields <= valid_fields:
            invalid_fields = ", ".join(list(used_fields - valid_fields))
            raise InvalidUse("Unsupported names of fields: %s." % invalid_fields)
        post_data = 'id: queue/%s\n' % str(queue_id)
        for key, val in iteritems(kwargs):
            post_data += '%s: %s\n' % (key, val)
        msg = self.__request('edit', post_data={'content': post_data})
        msgs = msg.split('\n')
        if (self.__get_status_code(msg) == 200) and (len(msgs) > 2):
            match = self.RE_PATTERNS['queue_pattern'].match(msgs[2])
            if match:
                return match.group(1)
        return False


    def create_queue(self, Name, **kwargs):
        """ Create queue (undocumented API feature).
        
        :param Name: Queue name (required)
        :param kwargs: Optional fields to set (see edit_queue)
        :returns: ID of new queue or False when create fails
        :raises BadRequest: When queue already exists
        :raises InvalidUse: When invalid fields are set
        """

        return int(self.edit_queue('new', Name=Name, **kwargs))


    def get_links(self, ticket_id):
        """ Gets the ticket links for a single ticket.
        
        :param ticket_id: ticket ID
        :returns: Links as lists of strings in dictionary with these keys
                  (just those which are defined):

                      * id
                      * Members
                      * MemberOf
                      * RefersTo
                      * ReferredToBy
                      * DependsOn
                      * DependedOnBy

                  None is returned if ticket does not exist.
        :raises UnexpectedMessageFormat: In case that returned status code is not 200
        """
        msg = self.__request('ticket/%s/links/show' % (str(ticket_id),))

        status_code = self.__get_status_code(msg)
        if(status_code == 200):
            pairs = {}
            msg = msg.split('\n')
            if (len(msg) > 2) and self.RE_PATTERNS['does_not_exist_pattern'].match(msg[2]):
                return None
            i = 2
            while i < len(msg):
                if ': ' in msg[i]:
                    key, link = msg[i].split(': ', 1)
                    links = [link.strip()]
                    j = i + 1
                    pad = len(key) + 2
                    # loop over next lines for the same key 
                    while (j < len(msg)) and msg[j].startswith(' ' * pad):
                        links[-1] = links[-1][:-1] # remove trailing comma from previous item
                        links.append(msg[j][pad:].strip())
                        j += 1
                    pairs[key] = links
                    i = j - 1
                i += 1
            return pairs
        else:
            raise UnexpectedMessageFormat('Received status code is %d instead of 200.' % status_code)

    def edit_ticket_links(self, ticket_id, **kwargs):
        """ Edit ticket links.

        .. warning:: This method is deprecated in favour of edit_link method, because
           there exists bug in RT 3.8 REST API causing mapping created links to
           ticket/1. The only drawback is that edit_link cannot process multiple
           links all at once.
    
        :param ticket_id: ID of ticket to edit
        :keyword kwargs: Other arguments possible to set: DependsOn,
                         DependedOnBy, RefersTo, ReferredToBy, Members,
                         MemberOf. Each value should be either ticker ID or
                         external link. Int types are converted. Use empty
                         string as value to delete existing link.
        :returns: ``True``
                      Operation was successful
                  ``False``
                      Ticket with given ID does not exist or unknown parameter
                      was set (in this case all other valid fields are changed)
        """
        post_data = ''
        for key in kwargs:
            post_data += "%s: %s\n"%(key, str(kwargs[key]))
        msg = self.__request('ticket/%s/links' % (str(ticket_id),),
                             post_data={'content':post_data})
        state = msg.split('\n')[2]
        return self.RE_PATTERNS['links_updated_pattern'].match(state) is not None

    def edit_link(self, ticket_id, link_name, link_value, delete=False):
        """ Creates or deletes a link between the specified tickets (undocumented API feature).
    
        :param ticket_id: ID of ticket to edit
        :param link_name: Name of link to edit (DependsOn, DependedOnBy,
                          RefersTo, ReferredToBy, HasMember or MemberOf)
        :param link_value: Either ticker ID or external link.
        :param delete: if True the link is deleted instead of created
        :returns: ``True``
                      Operation was successful
                  ``False``
                      Ticket with given ID does not exist or link to delete is
                      not found
        :raises InvalidUse: When none or more then one links are specified. Also
                            when wrong link name is used.
        """
        valid_link_names = set(('dependson', 'dependedonby', 'refersto', 
                           'referredtoby', 'hasmember', 'memberof'))
        if not link_name.lower() in valid_link_names:
            raise InvalidUse("Unsupported name of link.")
        post_data = {'rel': link_name.lower(),
                     'to': link_value,
                     'id': ticket_id,
                     'del': 1 if delete else 0
                    }
        msg = self.__request('ticket/link', post_data=post_data)
        state = msg.split('\n')[2]
        if delete:
            return self.RE_PATTERNS['deleted_link_pattern'].match(state) is not None
        else:
            return self.RE_PATTERNS['created_link_pattern'].match(state) is not None

    def merge_ticket(self, ticket_id, into_id):
        """ Merge ticket into another (undocumented API feature).
    
        :param ticket_id: ID of ticket to be merged
        :param into: ID of destination ticket
        :returns: ``True``
                      Operation was successful
                  ``False``
                      Either origin or destination ticket does not
                      exist or user does not have ModifyTicket permission.
        """
        msg = self.__request('ticket/%s/merge/%s' % (str(ticket_id),
                                                     str(into_id)))
        state = msg.split('\n')[2]
        return self.RE_PATTERNS['merge_successful_pattern'].match(state) is not None

    def take(self, ticket_id):
        """ Take ticket

        :param ticket_id: ID of ticket to be merged
        :returns: ``True``
                      Operation was successful
                  ``False``
                      Either the ticket does not exist or user does not 
                      have TakeTicket permission.
        """
        post_data = {'content':"Action: take"}
        msg = self.__request('ticket/%s/take' % (str(ticket_id)))
        return self.__get_status_code(msg) == 200

    def steal(self, ticket_id):
        """ Steal ticket

        :param ticket_id: ID of ticket to be merged
        :returns: ``True``
                      Operation was successful
                  ``False``
                      Either the ticket does not exist or user does not 
                      have StealTicket permission.
        """
        post_data = {'content':"Action: steal"}
        msg = self.__request('ticket/%s/take' % (str(ticket_id)))
        return self.__get_status_code(msg) == 200

    def untake(self, ticket_id):
        """ Untake ticket

        :param ticket_id: ID of ticket to be merged
        :returns: ``True``
                      Operation was successful
                  ``False``
                      Either the ticket does not exist or user does not 
                      own the ticket.
        """
        post_data = {'content':"Action: untake"}
        msg = self.__request('ticket/%s/take' % (str(ticket_id)))
        return self.__get_status_code(msg) == 200

