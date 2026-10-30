/* Loaded from a trusted local file. One observer per browser document. */
(() => {
  if (window.studyMateA11y) return;
  let pending = null;
  let lastNotice = null;
  let scheduled = false;
  const attr = (element, name, value) => {
    if (element && element.getAttribute(name) !== value) element.setAttribute(name, value);
  };
  const heading = () => document.querySelector('[data-testid="stMain"] h1');
  const region = document.createElement('div');
  region.id = 'studymate-announcements';
  region.className = 'studymate-sr-only';
  region.setAttribute('role', 'status');
  region.setAttribute('aria-live', 'polite');
  region.setAttribute('aria-atomic', 'true');
  document.body.appendChild(region);
  const skip = document.querySelector('[data-testid="stSkipToContent"]') || document.createElement('a');
  skip.classList.add('studymate-skip');
  skip.href = '#studymate-main-heading';
  skip.textContent = '跳到主要内容';
  skip.addEventListener('click', event => {
    event.preventDefault();
    event.stopPropagation();
    heading()?.focus();
  });
  if (!skip.isConnected) document.body.prepend(skip);

  function decorate() {
    attr(document.documentElement, 'lang', 'zh-CN');
    attr(document.querySelector('[data-testid="stMain"]'), 'role', 'main');
    const title = heading();
    attr(title, 'tabindex', '-1');
    if (title && !title.id) attr(title, 'id', 'studymate-main-heading');
    if (title) attr(skip, 'href', '#' + title.id);
    document.querySelectorAll('[data-testid="stSpinner"]').forEach(element => {
      attr(element, 'role', 'status');
      attr(element, 'aria-live', 'polite');
    });
    document.querySelectorAll('[class*="st-key-document_status_"]').forEach(element => {
      attr(element, 'role', 'status');
      attr(element, 'aria-live', 'polite');
      attr(element, 'aria-atomic', 'true');
    });
    // Framework-generated upload buttons retain their visible English text in
    // the accessible name (WCAG label-in-name), with Chinese purpose added.
    document.querySelectorAll('[data-testid="stFileUploader"] button').forEach(button => {
      const text = (button.querySelector('[data-testid="stMarkdownContainer"]') || button).textContent.trim();
      if (text === 'Browse files' || text === 'Upload') attr(button, 'aria-label', '选择 PDF 文件（' + text + '）');
      if (button.getAttribute('aria-label') === 'Remove file') attr(button, 'aria-label', '移除已选择的文件');
    });
    document.querySelectorAll('[data-testid="stFileUploader"]').forEach(uploader => {
      const label = uploader.querySelector('[data-testid="stWidgetLabel"]')?.textContent.trim();
      if (label) attr(uploader.querySelector('input[type="file"]'), 'aria-label', label);
    });
  }

  function restoreFocus() {
    if (!pending) return;
    const notice = pending;
    const owner = document.querySelector('.' + CSS.escape('st-key-' + notice.focus));
    if (notice.focus === 'page_navigation' && document.activeElement !== document.body &&
        !owner?.contains(document.activeElement)) {
      pending = null;
      return;
    }
    const target = notice.focus === 'heading' ? heading() :
      owner?.querySelector('input[type="radio"]:checked') || owner?.querySelector('button,input,textarea,select');
    if (target && !target.disabled) {
      target.focus();
      pending = null;
    } else if (performance.now() > notice.deadline) {
      heading()?.focus();
      pending = null;
    } else {
      setTimeout(restoreFocus, 50);
      return;
    }
  }

  function refresh() {
    scheduled = false;
    decorate();
  }
  new MutationObserver(() => {
    if (!scheduled) { scheduled = true; requestAnimationFrame(refresh); }
  }).observe(document.body, {childList: true, subtree: true});
  // A user who has continued navigating must never have focus stolen later.
  document.addEventListener('keydown', () => { pending = null; }, true);
  document.addEventListener('pointerdown', () => { pending = null; }, true);
  window.studyMateA11y = {
    update(notice) {
      decorate();
      if (!notice || notice.id === lastNotice) return;
      lastNotice = notice.id;
      region.textContent = '';
      pending = {...notice, deadline: performance.now() + 1000};
      requestAnimationFrame(restoreFocus);
      setTimeout(() => {
        if (lastNotice === notice.id) region.textContent = notice.message;
      }, 150);
    }
  };
  decorate();
})();
