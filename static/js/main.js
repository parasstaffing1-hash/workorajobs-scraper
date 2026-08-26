// Workora Jobs - Main JavaScript
document.addEventListener('DOMContentLoaded', function() {
  // Search form
  const searchForm = document.getElementById('hero-search-form');
  if (searchForm) {
    searchForm.addEventListener('submit', function(e) {
      e.preventDefault();
      const q = document.getElementById('search-q').value;
      const l = document.getElementById('search-location').value;
      let url = '/jobs?q=' + encodeURIComponent(q);
      if (l) url += '&location=' + encodeURIComponent(l);
      window.location.href = url;
    });
  }

  // Save job buttons
  document.querySelectorAll('[data-save-job]').forEach(btn => {
    btn.addEventListener('click', async function(e) {
      e.preventDefault();
      const slug = this.dataset.saveJob;
      const action = this.dataset.action || 'save';
      try {
        const resp = await fetch('/api/save-job', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({slug, action})
        });
        const data = await resp.json();
        if (data.status === 'saved') {
          this.textContent = '★ Saved';
          this.classList.add('btn-warning');
          this.classList.remove('btn-outline');
        } else {
          this.textContent = '☆ Save';
          this.classList.remove('btn-warning');
          this.classList.add('btn-outline');
        }
      } catch(e) { window.location.href = '/login'; }
    });
  });

  // Cookie consent
  if (!localStorage.getItem('cookies_accepted')) {
    var c = document.getElementById('cookie-consent');
    if (c) c.style.display = 'flex';
  }

  // Close cookie banner
  document.querySelectorAll('[data-dismiss="cookie"]').forEach(btn => {
    btn.addEventListener('click', function() {
      localStorage.setItem('cookies_accepted', '1');
      var c = document.getElementById('cookie-consent');
      if (c) c.style.display = 'none';
    });
  });

  // Time filter
  const timeSelect = document.getElementById('time-filter');
  if (timeSelect) {
    timeSelect.addEventListener('change', function() {
      const params = new URLSearchParams(window.location.search);
      if (this.value) params.set('fresh', this.value);
      else params.delete('fresh');
      window.location.search = params.toString();
    });
  }

  // Source filter
  const sourceSelect = document.getElementById('source-filter');
  if (sourceSelect) {
    sourceSelect.addEventListener('change', function() {
      const params = new URLSearchParams(window.location.search);
      if (this.value) params.set('source', this.value);
      else params.delete('source');
      window.location.search = params.toString();
    });
  }
});
