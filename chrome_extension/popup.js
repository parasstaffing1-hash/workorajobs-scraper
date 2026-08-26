// LeadFlow Job Saver - Popup Script

document.addEventListener('DOMContentLoaded', () => {
  const saveBtn = document.getElementById('saveBtn');
  const applyBtn = document.getElementById('applyBtn');
  const statusEl = document.getElementById('status');
  const titleEl = document.getElementById('jobTitle');
  const companyEl = document.getElementById('jobCompany');
  const tagsInput = document.getElementById('tags');
  const notesInput = document.getElementById('notes');
  const apiUrlInput = document.getElementById('apiUrl');

  let currentTab = null;
  let jobData = {};

  // Load saved settings
  chrome.storage.sync.get(['apiUrl'], (result) => {
    if (result.apiUrl) {
      apiUrlInput.value = result.apiUrl;
    }
  });

  // Get current tab info
  chrome.tabs.query({ active: true, currentWindow: true }, (tabs) => {
    currentTab = tabs[0];
    detectJobInfo(currentTab);
  });

  // Detect job information from the page
  function detectJobInfo(tab) {
    chrome.scripting.executeScript({
      target: { tabId: tab.id },
      func: detectJobData
    }, (results) => {
      if (results && results[0]) {
        jobData = results[0].result;
        titleEl.textContent = jobData.title || 'Job Title Not Found';
        companyEl.textContent = jobData.company
          ? `${jobData.company} | ${jobData.location || 'Location unknown'}`
          : 'Company not detected';
      }
    });
  }

  // Function to detect job data from the page (injected into page context)
  function detectJobData() {
    const data = {
      title: '',
      company: '',
      location: '',
      description: '',
      url: window.location.href,
      source: new URL(window.location.href).hostname
    };

    // Try common job title selectors
    const titleSelectors = [
      'h1.job-title', 'h1[data-testid="job-title"]',
      '.jobsearch-JobInfoHeader-title', '.topcard__flavor',
      '[data-test="jobTitle"]', '.job-title', '.posting-headline h2',
      'h1.post-title', '.job-title-text', 'h1', '.title'
    ];
    for (const sel of titleSelectors) {
      const el = document.querySelector(sel);
      if (el && el.textContent.trim()) {
        data.title = el.textContent.trim().substring(0, 200);
        break;
      }
    }

    // Try common company selectors
    const companySelectors = [
      '.company-name', '.employer-name', '.topcard__org-name',
      '[data-test="employer-short-name"]', '.company',
      '.job-company-name', '.org-name', 'h3.topcard__org-name',
      '.posting-headline h3', '.company-name-text'
    ];
    for (const sel of companySelectors) {
      const el = document.querySelector(sel);
      if (el && el.textContent.trim()) {
        data.company = el.textContent.trim();
        break;
      }
    }

    // Try common location selectors
    const locationSelectors = [
      '.location', '.job-location', '.topcard__flavor--bullet',
      '[data-testid="text-location"]', '.company-location',
      '.job-location-text', '.org-location'
    ];
    for (const sel of locationSelectors) {
      const el = document.querySelector(sel);
      if (el && el.textContent.trim()) {
        data.location = el.textContent.trim();
        break;
      }
    }

    // Try to get job description
    const descSelectors = [
      '.job-description', '.description', '#job-description',
      '[data-testid="jobDescriptionText"]', '.posting-body'
    ];
    for (const sel of descSelectors) {
      const el = document.querySelector(sel);
      if (el) {
        data.description = el.textContent.trim().substring(0, 500);
        break;
      }
    }

    // Fallback: try meta tags
    if (!data.title) {
      const ogTitle = document.querySelector('meta[property="og:title"]');
      if (ogTitle) data.title = ogTitle.content;
    }
    if (!data.company) {
      const ogSite = document.querySelector('meta[property="og:site_name"]');
      if (ogSite) data.company = ogSite.content;
    }

    return data;
  }

  // Save job to LeadFlow
  saveBtn.addEventListener('click', async () => {
    const apiUrl = apiUrlInput.value || 'http://localhost:8000';
    const tags = tagsInput.value.split(',').map(t => t.trim()).filter(Boolean);
    const notes = notesInput.value;

    const payload = {
      ...jobData,
      tags,
      notes,
      savedAt: new Date().toISOString()
    };

    try {
      saveBtn.disabled = true;
      saveBtn.textContent = 'Saving...';

      const response = await fetch(`${apiUrl}/api/jobs`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload)
      });

      if (response.ok) {
        statusEl.className = 'status success';
        statusEl.textContent = '✅ Job saved to LeadFlow!';
        saveBtn.textContent = '✅ Saved!';

        // Save settings
        chrome.storage.sync.set({ apiUrl: apiUrl.value });
      } else {
        throw new Error('Server returned ' + response.status);
      }
    } catch (error) {
      statusEl.className = 'status error';
      statusEl.textContent = '❌ Error: ' + error.message;
      saveBtn.disabled = false;
      saveBtn.textContent = '⭐ Save to LeadFlow';
    }
  });

  // Auto-apply
  applyBtn.addEventListener('click', async () => {
    statusEl.className = 'status success';
    statusEl.textContent = '🚀 Opening auto-apply...';

    // Send message to background script
    chrome.runtime.sendMessage({
      action: 'autoApply',
      url: currentTab.url,
      jobData: jobData
    });
  });
});
