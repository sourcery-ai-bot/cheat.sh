#!/usr/bin/env python
# vim: set encoding=utf-8
# pylint: disable=wrong-import-position,wrong-import-order

"""
Main server program.

Configuration parameters:

    path.internal.malformed
    path.internal.static
    path.internal.templates
    path.log.main
    path.log.queries
"""

from __future__ import print_function

import sys
if sys.version_info[0] < 3:
    reload(sys)
    sys.setdefaultencoding('utf8')


from gevent.monkey import patch_all
from gevent.pywsgi import WSGIServer
patch_all()

import sys
import logging
import os
import requests
import jinja2
from flask import Flask, request, send_from_directory, redirect, Response

sys.path.append(os.path.abspath(os.path.join(__file__, "..", "..", "lib")))
from config import CONFIG
from limits import Limits
from cheat_wrapper import cheat_wrapper
from post import process_post_request
from options import parse_args

from stateful_queries import save_query, last_query

if not os.path.exists(os.path.dirname(CONFIG["path.log.main"])):
    os.makedirs(os.path.dirname(CONFIG["path.log.main"]))
logging.basicConfig(
    filename=CONFIG["path.log.main"],
    level=logging.DEBUG,
    format='%(asctime)s %(message)s')

app = Flask(__name__) # pylint: disable=invalid-name
app.jinja_loader = jinja2.ChoiceLoader([
    app.jinja_loader,
    jinja2.FileSystemLoader(CONFIG["path.internal.templates"])])

LIMITS = Limits()

def is_html_needed(user_agent):
    """
    Basing on `user_agent`, return whether it needs HTML or ANSI
    """
    plaintext_clients = [
        'curl', 'wget', 'fetch', 'httpie', 'lwp-request', 'openbsd ftp', 'python-requests']
    return all(x not in user_agent for x in plaintext_clients)

def is_result_a_script(query):
    return query in [':cht.sh']

@app.route('/files/<path:path>')
def send_static(path):
    """
    Return static file `path`.
    Can be served by the HTTP frontend.
    """
    return send_from_directory(CONFIG["path.internal.static"], path)

@app.route('/favicon.ico')
def send_favicon():
    """
    Return static file `favicon.ico`.
    Can be served by the HTTP frontend.
    """
    return send_from_directory(CONFIG["path.internal.static"], 'favicon.ico')

@app.route('/malformed-response.html')
def send_malformed():
    """
    Return static file `malformed-response.html`.
    Can be served by the HTTP frontend.
    """
    dirname, filename = os.path.split(CONFIG["path.internal.malformed"])
    return send_from_directory(dirname, filename)

def log_query(ip_addr, found, topic, user_agent):
    """
    Log processed query and some internal data
    """
    log_entry = "%s %s %s %s\n" % (ip_addr, found, topic, user_agent)
    with open(CONFIG["path.log.queries"], 'ab') as my_file:
        my_file.write(log_entry.encode('utf-8'))

def get_request_ip(req):
    """
    Extract IP address from `request`
    """

    if req.headers.getlist("X-Forwarded-For"):
        ip_addr = req.headers.getlist("X-Forwarded-For")[0]
        if ip_addr.startswith('::ffff:'):
            ip_addr = ip_addr[7:]
    else:
        ip_addr = req.remote_addr
    if req.headers.getlist("X-Forwarded-For"):
        ip_addr = req.headers.getlist("X-Forwarded-For")[0]
        if ip_addr.startswith('::ffff:'):
            ip_addr = ip_addr[7:]
    else:
        ip_addr = req.remote_addr

    return ip_addr

def get_answer_language(request):
    """
    Return preferred answer language based on
    domain name, query arguments and headers
    """

    def _parse_accept_language(accept_language):
        languages = accept_language.split(",")
        locale_q_pairs = []

        for language in languages:
            try:
                if language.split(";")[0] == language:
                    # no q => q = 1
                    locale_q_pairs.append((language.strip(), "1"))
                else:
                    locale = language.split(";")[0].strip()
                    weight = language.split(";")[1].split("=")[1]
                    locale_q_pairs.append((locale, weight))
            except IndexError:
                pass

        return locale_q_pairs

    def _find_supported_language(accepted_languages):
        for lang_tuple in accepted_languages:
            lang = lang_tuple[0]
            if '-' in lang:
                lang = lang.split('-', 1)[0]
            return lang
        return None

    lang = None
    hostname = request.headers['Host']
    if hostname.endswith('.cheat.sh'):
        lang = hostname[:-9]

    if 'lang' in request.args:
        lang = request.args.get('lang')

    header_accept_language = request.headers.get('Accept-Language', '')
    if lang is None and header_accept_language:
        lang = _find_supported_language(
            _parse_accept_language(header_accept_language))

    return lang

def _proxy(*args, **kwargs):
    # print "method=", request.method,
    # print "url=", request.url.replace('/:shell-x/', ':3000/')
    # print "headers=", {key: value for (key, value) in request.headers if key != 'Host'}
    # print "data=", request.get_data()
    # print "cookies=", request.cookies
    # print "allow_redirects=", False

    url_before, url_after = request.url.split('/:shell-x/', 1)
    url = f'{url_before}:3000/'

    if 'q' in request.args:
        url_after = '?' + "&".join(f"arg={x}" for x in request.args['q'].split())

    url += url_after
    print(url)
    print(request.get_data())
    resp = requests.request(
        method=request.method,
        url=url,
        headers={key: value for (key, value) in request.headers if key != 'Host'},
        data=request.get_data(),
        cookies=request.cookies,
        allow_redirects=False)

    excluded_headers = ['content-encoding', 'content-length', 'transfer-encoding', 'connection']
    headers = [(name, value) for (name, value) in resp.raw.headers.items()
               if name.lower() not in excluded_headers]

    return Response(resp.content, resp.status_code, headers)


@app.route("/", methods=['GET', 'POST'])
@app.route("/<path:topic>", methods=["GET", "POST"])
def answer(topic=None):
    """
    Main rendering function, it processes incoming weather queries.
    Depending on user agent it returns output in HTML or ANSI format.

    Incoming data:
        request.args
        request.headers
        request.remote_addr
        request.referrer
        request.query_string
    """

    user_agent = request.headers.get('User-Agent', '').lower()
    html_needed = is_html_needed(user_agent)
    options = parse_args(request.args)

    if (
        topic
        in [
            'apple-touch-icon-precomposed.png',
            'apple-touch-icon.png',
            'apple-touch-icon-120x120-precomposed.png',
        ]
        or topic is not None
        and any(topic.endswith(f'/{x}') for x in ['favicon.ico'])
    ):
        return ''

    request_id = request.cookies.get('id')
    if topic is not None and topic.lstrip('/') == ':last':
        if request_id:
            topic = last_query(request_id)
        else:
            return "ERROR: you have to set id for your requests to use /:last\n"
    elif request_id:
        save_query(request_id, topic)

    if request.method == 'POST':
        process_post_request(request, html_needed)
        return redirect("/") if html_needed else "OK\n"
    if 'topic' in request.args:
        return redirect(f"/{request.args.get('topic')}")

    if topic is None:
        topic = ":firstpage"

    if topic.startswith(':shell-x/'):
        return _proxy()
        #return requests.get('http://127.0.0.1:3000'+topic[8:]).text

    if lang := get_answer_language(request):
        options['lang'] = lang

    ip_address = get_request_ip(request)
    if '+' in topic:
        if not_allowed := LIMITS.check_ip(ip_address):
            return "429 %s\n" % not_allowed, 429

    html_is_needed = is_html_needed(user_agent) and not is_result_a_script(topic)
    output_format = 'html' if html_is_needed else 'ansi'
    result, found = cheat_wrapper(topic, request_options=options, output_format=output_format)
    if 'Please come back in several hours' in result and html_is_needed:
        return open(os.path.join(CONFIG["path.internal.malformed"])).read()
    log_query(ip_address, found, topic, user_agent)
    return result if html_is_needed else Response(result, mimetype='text/plain')


if __name__ == '__main__':
    # Serving cheat.sh with `gevent`
    if '--debug' in sys.argv:
        # Not all debug mode features are available under `gevent`
        # https://github.com/pallets/flask/issues/3825
        app.debug = True

    if 'CHEATSH_PORT' in os.environ:
        PORT = int(os.environ.get('CHEATSH_PORT'))
    else:
        PORT = CONFIG['server.port']

    SRV = WSGIServer((CONFIG['server.bind'], PORT), app) # log=None)
    print(f"Starting gevent server on {SRV.address[0]}:{SRV.address[1]}")
    SRV.serve_forever()
