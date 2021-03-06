# ***** BEGIN LICENSE BLOCK *****
# Version: MPL 1.1/GPL 2.0/LGPL 2.1
#
# The contents of this file are subject to the Mozilla Public License Version
# 1.1 (the "License"); you may not use this file except in compliance with
# the License. You may obtain a copy of the License at
# http://www.mozilla.org/MPL/
#
# Software distributed under the License is distributed on an "AS IS" basis,
# WITHOUT WARRANTY OF ANY KIND, either express or implied. See the License
# for the specific language governing rights and limitations under the
# License.
#
# The Original Code is Sync Server
#
# The Initial Developer of the Original Code is the Mozilla Foundation.
# Portions created by the Initial Developer are Copyright (C) 2010
# the Initial Developer. All Rights Reserved.
#
# Contributor(s):
#   Tarek Ziade (tarek@mozilla.com)
#
# Alternatively, the contents of this file may be used under the terms of
# either the GNU General Public License Version 2 or later (the "GPL"), or
# the GNU Lesser General Public License Version 2.1 or later (the "LGPL"),
# in which case the provisions of the GPL or the LGPL are applicable instead
# of those above. If you wish to allow use of your version of this file only
# under the terms of either the GPL or the LGPL, and not to allow others to
# use your version of this file under the terms of the MPL, indicate your
# decision by deleting the provisions above and replace them with the notice
# and other provisions required by the GPL or the LGPL. If you do not delete
# the provisions above, a recipient may use your version of this file under
# the terms of any one of the MPL, the GPL or the LGPL.
#
# ***** END LICENSE BLOCK *****
"""
Application entry point.
"""
import traceback

from paste.translogger import TransLogger
from paste.exceptions.errormiddleware import ErrorMiddleware

from routes import Mapper

from webob.dec import wsgify
from webob.exc import HTTPNotFound, HTTPBadRequest, HTTPServiceUnavailable
from webob import Response

from services.util import (convert_config, CatchErrorMiddleware, round_time,
                           BackendError)
from services import logger
from services.wsgiauth import Authentication
from services.controllers import StandardController


class SyncServerApp(object):
    """ Dispatches the request to the right controller by using Routes.
    """
    def __init__(self, urls, controllers, config=None,
                 auth_class=Authentication):
        self.mapper = Mapper()
        if config is not None:
            self.config = config
        else:
            self.config = {}

        # global config
        self.retry_after = self.config.get('global.retry_after', 1800)

        # heartbeat page
        self.heartbeat_page = self.config.get('global.heartbeat_page',
                                              '__heartbeat__')

        # debug page, if any
        self.debug_page = self.config.get('global.debug_page')

        # loading the authentication tool
        self.auth = None if auth_class is None else auth_class(self.config)

        # loading and connecting controllers
        self.controllers = dict([(name, klass(self)) for name, klass in
                                 controllers.items()])

        for url in urls:
            if len(url) == 4:
                verbs, match, controller, action = url
                extras = {}
            elif len(url) == 5:
                verbs, match, controller, action, extras = url
            else:
                msg = "Each URL description needs 4 or 5 elements. Got %s" \
                    % str(url)
                raise ValueError(msg)

            if isinstance(verbs, str):
                verbs = [verbs]

            self.mapper.connect(None, match, controller=controller,
                                action=action, conditions=dict(method=verbs),
                                **extras)

        # loads host-specific configuration
        self._host_configs = {}

        # heartbeat & debug pages
        self.standard_controller = StandardController(self)

        # rehooked overridable points so they can be overridden in the base app
        self.standard_controller._debug_server = self._debug_server
        self.standard_controller._check_server = self._check_server

    def _before_call(self, request):
        return {}

    def _host_specific(self, host, config):
        """Will compute host-specific requests"""
        if host in self._host_configs:
            return self._host_configs[host]

        # overrides the original value with the host-specific value
        host_section = 'host:%s.' % host
        host_config = {}
        overriden_keys = []
        for key, value in config.items():
            if key in overriden_keys:
                continue

            if key.startswith(host_section):
                key = key[len(host_section):]
                overriden_keys.append(key)
            host_config[key] = value

        self._host_configs[host] = host_config
        return host_config

    #
    # Debug & heartbeat pages
    #
    def _debug_server(self, request):
        return []

    def _check_server(self, request):
        pass

    def _debug(self, request):
        return self.standard_controller._debug(request)

    def _heartbeat(self, request):
        return self.standard_controller._heartbeat(request)

    #
    # entry point
    #
    @wsgify
    def __call__(self, request):
        if request.method in ('HEAD',):
            raise HTTPBadRequest('"%s" not supported' % request.method)

        request.server_time = round_time()

        # gets request-specific config
        request.config = self._host_specific(request.host, self.config)

        # pre-hook
        before_headers = self._before_call(request)

        # XXX
        # removing the trailing slash - ambiguity on client side
        url = request.path_info.rstrip('/')
        if url != '':
            request.environ['PATH_INFO'] = request.path_info = url

        if (self.heartbeat_page is not None and
            url == '/%s' % self.heartbeat_page):
            return self._heartbeat(request)

        if self.debug_page is not None and url == '/%s' % self.debug_page:
            return self._debug(request)

        match = self.mapper.routematch(environ=request.environ)

        if match is None:
            return HTTPNotFound()

        match, __ = match

        # authentication control
        if self.auth is not None:
            self.auth.check(request, match)

        function = self._get_function(match['controller'], match['action'])
        if function is None:
            raise HTTPNotFound('Unkown URL %r' % request.path_info)

        # extracting all the info from the headers and the url
        request.sync_info = match

        # the GET mapping is filled on GET and DELETE requests
        if request.method in ('GET', 'DELETE'):
            params = dict(request.GET)
        else:
            params = {}

        try:
            result = function(request, **params)
        except BackendError:
            err = traceback.format_exc()
            logger.error(err)
            raise HTTPServiceUnavailable(retry_after=self.retry_after)

        if isinstance(result, basestring):
            response = getattr(request, 'response', None)
            if response is None:
                response = Response(result)
            elif isinstance(result, str):
                response.body = result
            else:
                # if it's not str it's unicode, which really shouldn't happen
                module = getattr(function, '__module__', 'unknown')
                name = getattr(function, '__name__', 'unknown')
                logger.warn('Unicode response returned from: %s - %s'
                            % (module, name))
                response.unicode_body = result
        else:
            # result is already a Response
            response = result

        # setting up the X-Weave-Timestamp
        response.headers['X-Weave-Timestamp'] = str(request.server_time)
        response.headers.update(before_headers)
        return response

    def _get_function(self, controller, action):
        """Return the action of the right controller."""
        try:
            controller = self.controllers[controller]
        except KeyError:
            return None
        return getattr(controller, action, None)


def set_app(urls, controllers, klass=SyncServerApp, auth_class=Authentication,
            wrapper=None):
    """make_app factory."""
    def make_app(global_conf, **app_conf):
        """Returns a Sync Server Application."""
        global_conf.update(app_conf)
        params = convert_config(global_conf)
        app = klass(urls, controllers, params, auth_class)

        if params.get('debug', False):
            app = TransLogger(app, logger_name='syncserver',
                              setup_console_handler=True)

        if params.get('profile', False):
            from repoze.profile.profiler import AccumulatingProfileMiddleware
            app = AccumulatingProfileMiddleware(app,
                                          log_filename='profile.log',
                                          cachegrind_filename='cachegrind.out',
                                          discard_first_request=True,
                                          flush_at_shutdown=True,
                                          path='/__profile__')

        if params.get('client_debug', False):
            # errors are displayed in the user client
            app = ErrorMiddleware(app, debug=True,
                                  show_exceptions_in_wsgi_errors=True)
        else:
            # errors are logged and a 500 is returned with an empty body
            # to avoid any security whole
            app = CatchErrorMiddleware(app, logger_name='syncserver')

        if wrapper is not None:
            app = wrapper(app)
        return app
    return make_app
