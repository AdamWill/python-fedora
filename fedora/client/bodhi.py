#!/usr/bin/python -tt
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; version 2 of the License.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU Library General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program; if not, write to the Free Software
# Foundation, Inc., 59 Temple Place - Suite 330, Boston, MA 02111-1307, USA.
#
# Copyright 2007  Red Hat, Inc
# Authors: Luke Macken <lmacken@redhat.com>

import re
import sys
import koji
import logging
import urllib2

from yum import YumBase
from textwrap import wrap
from os.path import join, expanduser, exists
from getpass import getpass, getuser
from optparse import OptionParser
from ConfigParser import ConfigParser

from fedora.client import BaseClient, AuthError, ServerError

__version__ = '$Revision: $'[11:-2]
__description__ = 'Command line tool for interacting with Bodhi'

BODHI_URL = 'http://publictest10.fedoraproject.org/updates/'
log = logging.getLogger(__name__)


class BodhiClient(BaseClient):

    def new(self, builds, opts):
        log.info("Creating new update for %s" % builds)
        params = {
                'builds'  : builds,
                'release' : opts.release.upper(),
                'type'    : opts.type,
                'bugs'    : opts.bugs,
                'notes'   : opts.notes
        }
        if hasattr(opts, 'request') and getattr(opts, 'request'):
            params['request'] = opts.request
        data = self.send_request('save', auth=True, req_params=params)
        log.info(data['tg_flash'])
        if data.has_key('update'):
            log.info(data['update'])

    def edit(self, builds, opts):
        log.info("Editing update for %s" % builds)
        params = {
                'builds'  : builds,
                'edited'  : builds,
                'release' : opts.release.upper(),
                'type'    : opts.type,
                'bugs'    : opts.bugs,
                'notes'   : opts.notes
        }
        if hasattr(opts, 'request') and getattr(opts, 'request'):
            params['request'] = opts.request
        data = self.send_request('save', auth=True, req_params=params)
        log.info(data['tg_flash'])
        if data.has_key('update'):
            log.info(data['update'])

    def list(self, opts, package=None, showcount=True):
        args = { 'tg_paginate_limit' : opts.limit }
        auth = False
        for arg in ('release', 'status', 'type', 'bugs', 'request', 'mine'):
            if getattr(opts, arg):
                args[arg] = getattr(opts, arg)
        if package:
            args['package'] = package[0]
        if args.has_key('mine'):
            auth = True
        data = self.send_request('list', req_params=args, auth=auth)
        if data.has_key('tg_flash') and data['tg_flash']:
            log.error(data['tg_flash'])
            sys.exit(-1)
        if data['num_items'] > 1:
            from pprint import pprint
            for update in data['updates']:
                pprint(update)
                log.info(' %s\t%s\t%s' % (update['title'], update['type'],
                                          update['status']))
        else:
            for update in data['updates']:
                log.info(self._update_str(update))
            if showcount:
                log.info("%d updates found (%d shown)" % (data['num_items'],
                                                          len(data['updates'])))

    def delete(self, update):
        params = { 'update' : update }
        data = self.send_request('delete', req_params=params, auth=True)
        log.info(data['tg_flash'])

    def __koji_session(self):
        config = ConfigParser()
        if exists(join(expanduser('~'), '.koji', 'config')):
            config.readfp(open(join(expanduser('~'), '.koji', 'config')))
        else:
            config.readfp(open('/etc/koji.conf'))
        cert = expanduser(config.get('koji', 'cert'))
        ca = expanduser(config.get('koji', 'ca'))
        serverca = expanduser(config.get('koji', 'serverca'))
        session = koji.ClientSession(config.get('koji', 'server'))
        session.ssl_login(cert=cert, ca=ca, serverca=serverca)
        return session

    koji_session = property(fget=__koji_session)

    def candidates(self, opts):
        """
        Display a list of candidate builds which could potentially be pushed
        as updates.  This is a very expensive operation.
        """
        data = self.send_request("dist_tags")
        for tag in [tag + '-updates-candidate' for tag in data['tags']]:
            for build in self.koji_session.listTagged(tag, latest=True):
                if build['owner_name'] == opts.username:
                    log.info("%-40s %-20s" % (build['nvr'], build['tag_name']))

    def testable(self, opts):
        """
        Display a list of installed updates that you have yet to test
        and provide feedback for.
        """
        fedora = file('/etc/fedora-release').readlines()[0].split()[2]
        if fedora == '7': fedora = 'c7'
        tag = 'dist-f%s-updates-testing' % fedora
        builds = self.koji_session.listTagged(tag, latest=True)

        yum = YumBase()
        yum.doConfigSetup(init_plugins=False)

        for build in builds:
            pkgs = yum.rpmdb.searchNevra(name=build['name'],
                                         epoch=None,
                                         ver=build['version'],
                                         rel=build['release'],
                                         arch=None)
            if len(pkgs):
                self.list(opts, package=[build['nvr']], showcount=False)

    def comment(self, opts, update):
        params = {
                'text'  : opts.comment,
                'karma' : opts.karma,
                'title' : update
        }
        data = self.send_request('comment', req_params=params, auth=True)
        if data['tg_flash']:
            log.info(data['tg_flash'])
        if data.has_key('update'):
            log.info(data['update'])

    def request(self, opts, update):
        params = { 'action' : opts.request, 'update' : update }
        data = self.send_request('request', req_params=params, auth=True)
        log.info(data['tg_flash'])
        if data.has_key('update'):
            log.info(data['update'])

    def masher(self):
        data = self.send_request('admin/masher', auth=True)
        log.info(data['masher_str'])

    def push(self, opts):
        data = self.send_request('admin/push', auth=True)
        log.info("[ %d Pending Requests ]" % len(data['updates']))
        for status in ('testing', 'stable', 'obsolete'):
            updates = filter(lambda x: x['request'] == status, data['updates'])
            if len(updates):
                log.info("\n" + status.title() + "\n========")
                for update in updates:
                    log.info("%s" % update['title'])

        ## Confirm that we actually want to push these updates
        sys.stdout.write("\nPush these updates? [n]")
        sys.stdout.flush()
        yes = sys.stdin.readline().strip()
        if yes.lower() in ('y', 'yes'):
            log.info("Pushing!")
            self.send_request('admin/mash', auth=True, req_params={
                    'updates' : [u['title'] for u in data['updates']] })

    def parse_file(self,opts):
        regex = re.compile(r'^(BUG|bug|TYPE|type|REQUEST|request)=(.*$)')
        types = {'S':'security','B':'bugfix','E':'enhancement'}
        requests = {'T':'testing','S':'stable'}
        def _split(var, delim):
            if var: return var.split(delim)
            else: return []
        notes = _split(opts.notes,'\n')
        bugs = _split(opts.bugs,',')
        log.info("Reading from %s " % opts.input_file)
        if exists(opts.input_file):
            f = open(opts.input_file)
            lines = f.readlines()
            f.close()
            for line in lines:
                if line[0] == ':' or line[0] == '#':
                    continue
                src = regex.search(line)
                if src:
                    cmd,para = tuple(src.groups())
                    cmd = cmd.upper()
                    if cmd == 'BUG':
                        para = [p for p in para.split(' ')]
                        bugs.extend(para)
                    elif cmd == 'TYPE':
                        opts.type = types[para.upper()]
                    elif cmd == 'REQUEST':
                        opts.request = requests[para.upper()]
                else: # This is notes
                    notes.append(line.strip())
        if notes:
            opts.notes = "\r\n".join(notes)
        if bugs:
            opts.bugs = ','.join(bugs)
        log.debug("Type : %s" % opts.type)
        log.debug("Request: %s" % opts.request)
        log.debug('Bugs:\n%s' % opts.bugs)
        log.debug('Notes:\n%s' % opts.notes)
        self.file_parsed = True

    def _update_str(self, update):
        """ Return a string representation of a given update """
        val = "%s\n%s\n%s\n" % ('=' * 80, '\n'.join(
            wrap(update['title'].replace(',', ', '), width=80,
                 initial_indent=' '*5, subsequent_indent=' '*5)), '=' * 80)
        if update['updateid']:
            val += "  Update ID: %s\n" % update['updateid']
        val += """    Release: %s
     Status: %s
       Type: %s
      Karma: %d""" % (update['release']['long_name'], update['status'],
                      update['type'], update['karma'])
        if update['request'] != None:
            val += "\n    Request: %s" % update['request']
        if len(update['bugs']):
            bugs = ''
            i = 0
            for bug in update['bugs']:
                bugstr = '%s%s - %s\n' % (i and ' ' * 11 + ': ' or '',
                                          bug['bz_id'], bug['title'])
                bugs += '\n'.join(wrap(bugstr, width=67,
                                       subsequent_indent=' '*11+': ')) + '\n'
                i += 1
            bugs = bugs[:-1]
            val += "\n       Bugs: %s" % bugs
        if update['notes']:
            notes = wrap(update['notes'], width=67,
                         subsequent_indent=' ' * 11 + ': ')
            val += "\n      Notes: %s" % '\n'.join(notes)
        val += """
  Submitter: %s
  Submitted: %s\n""" % (update['submitter'], update['date_submitted'])
        if len(update['comments']):
            val += "   Comments: "
            comments = []
            for comment in update['comments']:
                if comment['anonymous']:
                    anonymous = " (unauthenticated)"
                else:
                    anonymous = ""
                comments.append("%s%s%s - %s (karma %s)" % (' ' * 13,
                                comment['author'], anonymous,
                                comment['timestamp'], comment['karma']))
                if comment['text']:
                    text = wrap(comment['text'], initial_indent=' ' * 13,
                                subsequent_indent=' ' * 13, width=67)
                    comments.append('\n'.join(text))
            val += '\n'.join(comments).lstrip() + '\n'
        if update['updateid']:
            val += "\n  %s\n" % ('%s%s/%s' % (BODHI_URL,
                                              update['release']['name'],
                                              update['updateid']))
        else:
            val += "\n  %s\n" % ('%s%s' % (BODHI_URL, update['title']))
        return val

def setup_logger():
    global log
    sh = logging.StreamHandler()
    level = opts.verbose and logging.DEBUG or logging.INFO
    log.setLevel(level)
    sh.setLevel(level)
    format = logging.Formatter("%(message)s")
    sh.setFormatter(format)
    log.addHandler(sh)


if __name__ == '__main__':
    usage = "usage: %prog [options] [build|package]"
    parser = OptionParser(usage, description=__description__,
                          version=__version__)

    ## Actions
    parser.add_option("-n", "--new", action="store_true", dest="new",
                      help="Submit a new update")
    parser.add_option("-e", "--edit", action="store_true", dest="edit",
                      help="Edit an existing update")
    parser.add_option("-M", "--masher", action="store_true", dest="masher",
                      help="Display the status of the Masher (releng only)")
    parser.add_option("-P", "--push", action="store_true", dest="push",
                      help="Display and push any pending updates (releng only)")
    parser.add_option("-d", "--delete", action="store_true", dest="delete",
                      help="Delete an update")
    parser.add_option("", "--file", action="store", type="string",
                      dest="input_file", help="Get update details from a file")
    parser.add_option("-m", "--mine", action="store_true", dest="mine",
                      help="Display a list of your updates")
    parser.add_option("-C", "--candidates", action="store_true",
                      help="Display a list of your update candidates",
                      dest="candidates")
    parser.add_option("-T", "--testable", action="store_true",
                      help="Display a list of installed updates that you "
                           "could test and provide feedback for")
    parser.add_option("-c", "--comment", action="store", dest="comment",
                      help="Comment on an update")
    parser.add_option("-k", "--karma", action="store", dest="karma",
                      metavar="[+1|-1]", default=0,
                      help="Give karma to a specific update (default: 0)")
    parser.add_option("-R", "--request", action="store", dest="request",
                      metavar="STATE", help="Request that an action be "
                      "performed on an update [testing|stable|unpush|obsolete]")

    ## Details
    parser.add_option("-s", "--status", action="store", type="string",
                      dest="status", help="List [pending|testing|stable|"
                      "obsolete] updates")
    parser.add_option("-b", "--bugs", action="store", type="string",
                      dest="bugs", help="Specify any number of Bugzilla IDs "
                      "(--bugs=1234,5678)", default="")
    parser.add_option("-r", "--release", action="store", type="string",
                      dest="release", help="Specify a release [F7|F8]")
    parser.add_option("-N", "--notes", action="store", type="string",
                      dest="notes", help="Update notes", default="")
    parser.add_option("-t", "--type", action="store", type="string",
                      help="Update type [bugfix|security|enhancement]",
                      dest="type")
    parser.add_option("-u", "--username", action="store", type="string",
                      dest="username", default=getuser(),
                      help="Login username for bodhi")

    ## Output
    parser.add_option("-v", "--verbose", action="store_true", dest="verbose",
                      help="Show debugging messages")
    parser.add_option("-l", "--limit", action="store", type="int", dest="limit",
                      default=10, help="Maximum number of updates to return "
                      "(default: 10)")

    (opts, args) = parser.parse_args()
    setup_logger()

    bodhi = BodhiClient(BODHI_URL, username=opts.username, password=None,
                        debug=opts.verbose)

    def verify_args(args):
        if not args and len(args) != 1:
            log.error("Please specifiy a comma-separated list of builds")
            sys.exit(-1)

    while True:
        try:
            if opts.new:
                verify_args(args)
                if opts.input_file and not hasattr(bodhi, 'file_parsed'):
                    bodhi.parse_file(opts)
                if not opts.release:
                    log.error("Error: No release specified (ie: -r F8)")
                    sys.exit(-1)
                if not opts.type:
                    log.error("Error: No update type specified (ie: -t bugfix)")
                    sys.exit(-1)
                bodhi.new(args[0], opts)
            elif opts.edit:
                verify_args(args)
                bodhi.edit(args[0], opts)
            elif opts.request:
                verify_args(args)
                bodhi.request(opts, args[0])
            elif opts.delete:
                verify_args(args)
                bodhi.delete(args[0])
            elif opts.push:
                bodhi.push(opts)
            elif opts.masher:
                bodhi.masher()
            elif opts.testable:
                bodhi.testable(opts)
            elif opts.candidates:
                bodhi.candidates(opts)
            elif opts.comment or opts.karma:
                if not len(args) or not args[0]:
                    log.error("Please specify an update to comment on")
                    sys.exit(-1)
                bodhi.comment(opts, args[0])
            elif opts.status or opts.bugs or opts.release or opts.type or \
                 opts.mine or args:
                bodhi.list(opts, args)
            else:
                parser.print_help()
            break
        except AuthError:
            bodhi.password = getpass('Password for %s: ' % opts.username)
        except ServerError, e:
            log.error(e.message)
            sys.exit(-1)
        except urllib2.URLError, e:
            log.error(e)
            sys.exit(-1)
