/*
 * travel-vedit 모션 런타임 — 모든 템플릿이 공유하는 규약.
 *
 * 템플릿은 CSS 애니메이션 / Web Animations API 로만 움직인다.
 * 렌더러(Playwright)는 window.__vedit_seek(t) 로 시간을 한 프레임씩 옮기며 캡처하고,
 * 브라우저/앱 미리보기에서는 같은 seek 를 requestAnimationFrame 으로 반복 재생한다.
 * → 미리보기와 최종 렌더가 같은 코드 경로를 탄다.
 *
 * 템플릿이 쓸 수 있는 것
 *   VEDIT.params            : 기본값 + 주입값이 합쳐진 파라미터
 *   VEDIT.duration          : 길이(초)
 *   VEDIT.build(fn)         : DOM 구성 훅 (글자 쪼개기 등). fn(params) — 폰트 로드 전에 실행
 *   VEDIT.onSeek(fn)        : JS 로 계산하는 값(숫자 카운터 등)을 위한 훅. fn(t)
 *   [data-param="key"]      : textContent 로 파라미터 값 자동 주입
 *   [data-fit]              : 넘치면 글자 크기를 자동으로 줄임 (data-fit-min="40")
 *   CSS 변수                : --<param-key> (밑줄 → 하이픈), --duration,
 *                             폰트 파라미터는 --<key>-family / --<key>-weight 도 생성
 */
(function () {
  "use strict";

  var metaEl = document.getElementById("vedit-meta");
  var META = metaEl ? JSON.parse(metaEl.textContent) : {};
  var RENDER = window.__VEDIT_RENDER === true;
  var builders = [];
  var seekHooks = [];

  function hashParams() {
    try {
      var m = location.hash.match(/params=([^&]+)/);
      return m ? JSON.parse(decodeURIComponent(m[1])) : {};
    } catch (e) {
      return {};
    }
  }

  var params = {};
  Object.keys(META.params || {}).forEach(function (k) {
    params[k] = META.params[k]["default"];
  });
  Object.assign(params, hashParams(), window.PARAMS || {});
  var duration = Number(params.duration || META.duration || 4);

  window.VEDIT = {
    meta: META,
    params: params,
    duration: duration,
    render: RENDER,
    build: function (fn) { builders.push(fn); },
    onSeek: function (fn) { seekHooks.push(fn); },
  };

  // "Pretendard Black" → family "Pretendard", weight 900
  var WEIGHTS = {
    thin: 100, extralight: 200, light: 300, regular: 400, medium: 500,
    semibold: 600, bold: 700, extrabold: 800, black: 900, heavy: 900,
  };
  function splitFont(name) {
    var parts = String(name).trim().split(/\s+/);
    var last = parts[parts.length - 1].toLowerCase().replace(/[-_]/g, "");
    if (parts.length > 1 && WEIGHTS[last]) {
      return { family: parts.slice(0, -1).join(" "), weight: WEIGHTS[last] };
    }
    return { family: parts.join(" "), weight: null };
  }

  function cssKey(k) { return "--" + k.replace(/_/g, "-"); }

  function applyParams() {
    var style = document.documentElement.style;
    var fonts = [];
    Object.keys(params).forEach(function (k) {
      var v = params[k];
      if (typeof v !== "string" && typeof v !== "number") return;
      style.setProperty(cssKey(k), String(v));
      if (/^font/.test(k) && typeof v === "string") {
        var f = splitFont(v);
        style.setProperty(cssKey(k) + "-family", '"' + f.family.replace(/"/g, "") + '"');
        if (f.weight) style.setProperty(cssKey(k) + "-weight", String(f.weight));
        fonts.push(f);
      }
    });
    style.setProperty("--duration", String(duration));
    document.querySelectorAll("[data-param]").forEach(function (el) {
      var v = params[el.dataset.param];
      var text = v == null ? "" : String(v);
      el.textContent = text;
      el.classList.toggle("is-empty", !text.trim());
    });
    return fonts;
  }

  function fitText() {
    document.querySelectorAll("[data-fit]").forEach(function (el) {
      var min = Number(el.dataset.fitMin || 24);
      var size = parseFloat(getComputedStyle(el).fontSize);
      var guard = 0;
      while (el.scrollWidth > el.clientWidth + 1 && size > min && guard++ < 200) {
        size -= 2;
        el.style.fontSize = size + "px";
      }
    });
  }

  function seek(t) {
    var ms = Math.max(0, Math.min(t, duration)) * 1000;
    document.getAnimations().forEach(function (a) {
      a.pause();
      a.currentTime = ms;
    });
    seekHooks.forEach(function (fn) { fn(t); });
  }

  // 안전 영역(가장자리 5%) 이탈, 글자 넘침 점검. 렌더러가 '정지 구간'에서 호출한다.
  function check() {
    var W = 1920, H = 1080, issues = [];
    var stage = document.querySelector(".stage");
    var origin = stage ? stage.getBoundingClientRect() : { left: 0, top: 0 };
    var scale = stage ? stage.getBoundingClientRect().width / W : 1;
    document.querySelectorAll("[data-param]").forEach(function (el) {
      if (el.classList.contains("is-empty")) return;
      var r = el.getBoundingClientRect();
      if (!r.width && !r.height) return;
      var left = (r.left - origin.left) / scale, right = (r.right - origin.left) / scale;
      var top = (r.top - origin.top) / scale, bottom = (r.bottom - origin.top) / scale;
      var key = el.dataset.param;
      if (el.scrollWidth > el.clientWidth + 1) issues.push(key + ": 글자가 상자를 넘칩니다 (최소 크기까지 줄여도 김)");
      if (left < W * 0.05 - 1 || right > W * 0.95 + 1 || top < H * 0.05 - 1 || bottom > H * 0.95 + 1) {
        issues.push(key + ": 안전 영역(가장자리 5%) 밖으로 나갑니다");
      }
    });
    return issues;
  }

  function fitStage() {
    var stage = document.querySelector(".stage");
    if (!stage) return;
    var s = Math.min(innerWidth / 1920, innerHeight / 1080);
    stage.style.transform = s === 1 ? "" : "scale(" + s + ")";
  }

  function startPreview() {
    document.documentElement.classList.add("vedit-preview");
    fitStage();
    addEventListener("resize", fitStage);
    var t0 = performance.now();
    var loopLen = duration + 0.8; // 끝난 뒤 잠깐 비워 두고 반복
    function loop(now) {
      seek(((now - t0) / 1000) % loopLen);
      requestAnimationFrame(loop);
    }
    requestAnimationFrame(loop);
  }

  async function init() {
    var fonts = applyParams();
    for (var i = 0; i < builders.length; i++) await builders[i](params);
    void document.body.offsetHeight; // 레이아웃을 강제해 폰트 로드를 시작시킨다
    await Promise.all(fonts.map(function (f) {
      return document.fonts.load((f.weight || 400) + ' 64px "' + f.family + '"').catch(function () {});
    }));
    await document.fonts.ready;
    fitText();
    seek(0);
    window.__vedit_seek = seek;
    window.__vedit_check = check;
    window.__vedit_duration = duration;
    window.__vedit_hold = Number(META.hold != null ? META.hold : duration * 0.5);
    window.__vedit_ready = true;
    if (!RENDER) startPreview();
  }

  document.addEventListener("DOMContentLoaded", function () {
    init().catch(function (e) {
      window.__vedit_error = String(e && e.stack || e);
      throw e;
    });
  });
})();
