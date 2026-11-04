"""Focused shared-runtime transport checks; no external models or credentials."""
import base64
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
from unittest.mock import patch

import pytest

from pdfx import _vlhttp


@pytest.fixture
def shared(monkeypatch, tmp_path):
    pytest.importorskip('vision_client')
    clean = {key: value for key, value in os.environ.items()
             if not key.startswith(('PDFX_', 'VISION_', 'OCR_REFRESH_'))}
    clean['VISION_ENV_FILE'] = str(tmp_path / 'vision.env')
    clean['VISION_STATE_FILE'] = str(tmp_path / 'state.json')
    monkeypatch.setattr(_vlhttp, '_RUNTIME_LOADED', False)
    # The client loads new environment keys from the file. Restore those too,
    # so a later test never inherits a stopped fixture server or model chain.
    with patch.dict(os.environ, clean, clear=True):
        yield tmp_path / 'vision.env'


@pytest.fixture
def service():
    calls = []
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            body = self.rfile.read(int(self.headers['Content-Length']))
            calls.append((self.path, dict(self.headers), body))
            reply = ({'width': 100, 'height': 200, 'boxes': []}
                     if self.path == '/layout' else
                     {'choices': [{'message': {'content': 'text $x^2$'}}]})
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps(reply).encode())
        def log_message(self, *args):
            pass
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f'http://127.0.0.1:{server.server_port}', calls
    server.shutdown()
    thread.join()
    server.server_close()


def configure(path, url):
    path.write_text(f'VISION_BASE_URL={url}/v1\nVISION_API_KEY=fixture-key\nVISION_MODEL=fixture-model\n')


def test_shared_file_preserves_image_prompt_tokens_and_auth(shared, service):
    url, calls = service
    configure(shared, url)
    image = b'original-rendered-image-bytes'
    assert _vlhttp.post_image('vision-ocr', image, 'exact LaTeX prompt', 123, role='transcription') == 'text $x^2$'
    path, headers, body = calls[0]
    payload = json.loads(body)
    assert path == '/v1/chat/completions'
    assert headers['Authorization'] == 'Bearer fixture-key'
    assert payload['model'] == 'fixture-model'
    assert payload['max_tokens'] == 123
    assert payload['messages'][0]['content'] == [
        {'type': 'image_url', 'image_url': {'url': 'data:image/png;base64,' + base64.b64encode(image).decode()}},
        {'type': 'text', 'text': 'exact LaTeX prompt'},
    ]


@pytest.mark.parametrize("override", [False, True])
def test_structured_chain_selects_entry_without_losing_connection(shared, service, monkeypatch, override):
    url, calls = service
    shared.write_text('VISION_CHAIN=' + json.dumps([
        {'name': 'not-structured', 'model': 'same-model', 'base_url': url + '/wrong', 'api_key': 'wrong', 'structured': False},
        {'name': 'structured', 'model': 'same-model', 'base_url': url + '/right', 'api_key': 'right', 'structured': True},
    ]) + '\n')
    if override:
        monkeypatch.setenv('PDFX_TOC_MODEL', 'same-model')
    assert _vlhttp.configured_models('toc', structured=True) == ['structured']
    _vlhttp.post_image('structured', b'image', 'toc', role='toc')
    assert calls[0][0] == '/right/chat/completions'
    assert calls[0][1]['Authorization'] == 'Bearer right'


@pytest.mark.parametrize("selector", ["same-model", "unlisted-model"])
def test_pdfx_chain_preserves_shared_deployment_selection(shared, service, monkeypatch, selector):
    url, calls = service
    shared.write_text('VISION_CHAIN=' + json.dumps([
        {'name': 'not-structured', 'model': 'same-model', 'base_url': url + '/wrong', 'api_key': 'wrong', 'structured': False},
        {'name': 'structured', 'model': 'same-model', 'base_url': url + '/right', 'api_key': 'right', 'structured': True},
    ]) + '\n')
    monkeypatch.setenv('PDFX_VISION_CHAIN', json.dumps([{'model': selector, 'structured': True}]))
    models = _vlhttp.configured_models('toc', structured=True)
    if selector == 'same-model':
        assert models == ['structured']
        _vlhttp.post_image(models[0], b'image', 'toc', role='toc')
        assert calls[0][0] == '/right/chat/completions'
        assert calls[0][1]['Authorization'] == 'Bearer right'
    else:
        # An unlisted model inherits the first shared connection, including
        # its declared capability. It cannot bypass structured:false.
        assert models == []
        models = _vlhttp.configured_models('vision')
        assert models == ['unlisted-model']
        _vlhttp.post_image(models[0], b'image', 'ocr')
        assert json.loads(calls[0][2])['model'] == 'unlisted-model'


def test_legacy_endpoint_and_multipart_stay_distinct(shared, service, monkeypatch):
    url, calls = service
    configure(shared, url)
    monkeypatch.setenv('PDFX_TRANSCRIPTION_ENDPOINT', url + '/legacy')
    monkeypatch.setenv('PDFX_LAYOUT_ENDPOINT', url + '/layout')
    _vlhttp.post_image('legacy-model', b'image', '', role='transcription')
    assert calls[0][0] == '/legacy'
    assert json.loads(calls[0][2])['model'] == 'legacy-model'
    assert _vlhttp.post_multipart(b'original-png') == {'width': 100, 'height': 200, 'boxes': []}
    assert calls[1][0] == '/layout'
    assert b'original-png' in calls[1][2]
    assert calls[1][1]['Content-Type'].startswith('multipart/form-data;')


def test_refresh_default_uses_core_and_real_subprocess(shared, service, tmp_path):
    url, calls = service
    configure(shared, url)
    root = Path(__file__).resolve().parents[1]
    spec = importlib.util.spec_from_file_location('ocr_refresh_runtime', root / '.apm/skills/llm-ocr-refresh/ocr_refresh_jobs.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    executable = module.default_glance_path()
    assert executable.name == 'ocr_image.py'
    png = tmp_path / 'page.png'
    png.write_bytes(b'original-png')
    text, info, timed_out, error = module.run_glance(executable, png, 20)
    assert error == '' and not timed_out
    assert text.strip() == 'text $x^2$'
    assert info['model'] == 'fixture-model'
    assert calls[0][0] == '/v1/chat/completions'


def test_shared_only_skips_specialized_empty_prompt_layout(shared, service):
    url, calls = service
    configure(shared, url)
    from pdfx import ocr_docparser, ocr_qwen_vl
    with pytest.raises(_vlhttp.OCRError, match='dedicated layout'):
        ocr_docparser.ocr_png(b'page')
    assert calls == []
    assert ocr_qwen_vl.ocr_png(b'page') == 'text $x^2$'
    assert json.loads(calls[0][2])['messages'][0]['content'][1]['text'] == ocr_qwen_vl.PROMPT
