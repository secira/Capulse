/**
 * lazy_section.js — shell-first content loading (mirrors the F&O hybrid pattern).
 *
 * loadLazySection(containerId, url) fetches a server-rendered HTML fragment,
 * injects it into the container, then executes the fragment's <script> tags in
 * document order. External scripts (src=...) are awaited (onload) before the
 * next script runs so library dependencies (Chart.js, d3, marked) are ready
 * before inline scripts that use them.
 *
 * NOTE: inline scripts in the fragment must NOT wrap their logic in a
 * DOMContentLoaded listener — that event has already fired by the time the
 * fragment is injected. Use an IIFE instead.
 */
(function () {
    function errorMarkup() {
        return '' +
            '<div class="text-center" style="padding:48px 16px;font-family:\'Inter\',sans-serif;">' +
            '  <i class="fas fa-triangle-exclamation" style="font-size:32px;color:#f59e0b;"></i>' +
            '  <p style="margin:14px 0 16px;color:#666;">Could not load this section.</p>' +
            '  <button class="btn btn-sm btn-primary" onclick="location.reload()">' +
            '    <i class="fas fa-rotate-right me-1"></i>Retry</button>' +
            '</div>';
    }

    function runScripts(scripts) {
        // Execute sequentially; await external scripts so order is preserved.
        return scripts.reduce(function (chain, old) {
            return chain.then(function () {
                return new Promise(function (resolve) {
                    var s = document.createElement('script');
                    for (var i = 0; i < old.attributes.length; i++) {
                        var a = old.attributes[i];
                        s.setAttribute(a.name, a.value);
                    }
                    if (old.src) {
                        s.onload = resolve;
                        s.onerror = resolve; // keep going even if a CDN fails
                        s.textContent = old.textContent;
                        document.body.appendChild(s);
                    } else {
                        s.textContent = old.textContent;
                        document.body.appendChild(s);
                        resolve();
                    }
                });
            });
        }, Promise.resolve());
    }

    window.loadLazySection = function (containerId, url, onComplete) {
        var container = document.getElementById(containerId);
        if (!container) return;

        fetch(url, {
            credentials: 'same-origin',
            headers: { 'X-Requested-With': 'XMLHttpRequest' }
        })
            .then(function (resp) {
                // Session expired / not entitled: fetch follows the 302 to the
                // login or pricing page. Don't inject a full page into the
                // section — reload so the browser navigates there properly.
                if (resp.redirected) {
                    window.location.href = resp.url;
                    return null;
                }
                var ct = resp.headers.get('content-type') || '';
                if (!resp.ok || ct.indexOf('text/html') === -1) {
                    throw new Error('Bad response: ' + resp.status + ' ' + ct);
                }
                return resp.text();
            })
            .then(function (html) {
                if (html === null) return; // redirect already handled
                var tmp = document.createElement('div');
                tmp.innerHTML = html;

                var scripts = Array.prototype.slice.call(tmp.querySelectorAll('script'));
                scripts.forEach(function (s) { s.parentNode.removeChild(s); });

                container.innerHTML = '';
                while (tmp.firstChild) {
                    container.appendChild(tmp.firstChild);
                }

                return runScripts(scripts);
            })
            .then(function () {
                if (typeof onComplete === 'function') onComplete();
            })
            .catch(function (err) {
                console.error('lazy_section load failed:', err);
                container.innerHTML = errorMarkup();
                if (typeof onComplete === 'function') onComplete();
            });
    };
})();
