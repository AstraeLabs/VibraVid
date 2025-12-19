document.documentElement.setAttribute('data-theme', 'dark');

let statusIndicator = null;
let checkingSites = new Map();
let totalSites = 0;
let completedSites = 0;

function createStatusIndicator() {
  statusIndicator = document.createElement('div');
  statusIndicator.className = 'status-indicator';
  statusIndicator.innerHTML = `
    <div class="status-header">
      <div class="status-icon"></div>
      <span class="status-title">Loading Sites...</span>
    </div>
    <div class="status-text">Initializing site checks...</div>
    <div class="progress-bar">
      <div class="progress-fill"></div>
    </div>
    <div class="checking-sites"></div>
  `;
  document.body.appendChild(statusIndicator);
  return statusIndicator;
}

function updateStatusIndicator(status, text, progress = 0) {
  if (!statusIndicator) return;
  
  const statusIcon = statusIndicator.querySelector('.status-icon');
  const statusTitle = statusIndicator.querySelector('.status-title');
  const statusText = statusIndicator.querySelector('.status-text');
  const progressFill = statusIndicator.querySelector('.progress-fill');
  
  statusTitle.textContent = status;
  statusText.textContent = text;
  progressFill.style.width = `${progress}%`;
  
  if (status === 'Ready') {
    statusIcon.classList.add('ready');
    setTimeout(() => {
      statusIndicator.classList.add('hidden');
      setTimeout(() => statusIndicator.remove(), 300);
    }, 2000);
  }
}

function addSiteToCheck(siteName, siteUrl) {
  if (!statusIndicator) return;
  
  const checkingSitesContainer = statusIndicator.querySelector('.checking-sites');
  const siteElement = document.createElement('div');
  siteElement.className = 'checking-site';
  siteElement.innerHTML = `
    <span class="site-name">${siteName}</span>
    <div class="site-status-icon checking"></div>
  `;
  checkingSitesContainer.appendChild(siteElement);
  checkingSites.set(siteName, siteElement);
}

function updateSiteStatus(siteName, isOnline) {
  const siteElement = checkingSites.get(siteName);
  if (!siteElement) return;
  
  const statusIcon = siteElement.querySelector('.site-status-icon');
  statusIcon.classList.remove('checking');
  statusIcon.classList.add(isOnline ? 'online' : 'offline');
  siteElement.classList.add('completed', isOnline ? 'online' : 'offline');
  
  completedSites++;
  const progress = (completedSites / totalSites) * 100;
  updateStatusIndicator(
    'Checking Sites...', 
    `Checked ${completedSites}/${totalSites} sites`,
    progress
  );
}

async function checkSiteStatus(url, siteName) {
  try {
    console.log(`Checking status for: ${url}`);
    const controller = new AbortController();
    const timeoutId = setTimeout(() => controller.abort(), 3000);

    const response = await fetch(url, { 
      method: 'HEAD',
      mode: 'no-cors',
      signal: controller.signal,
      headers: {
        'Accept': 'text/html',
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/133.0.0.0'
      }
    });

    clearTimeout(timeoutId);

    const isOnline = response.type === 'opaque';
    console.log(`Site ${url} is ${isOnline ? 'online' : 'offline'} (Type: ${response.type})`);
    
    if (siteName) {
      updateSiteStatus(siteName, isOnline);
    }
    
    return isOnline;
  } catch (error) {
    console.log(`Error checking ${url}:`, error.message);
    
    if (siteName) {
      updateSiteStatus(siteName, false);
    }
    
    return false;
  }
}

const domainsJsonUrl = 'https://raw.githubusercontent.com/Arrowar/StreamingCommunity/refs/heads/main/.github/.domain/domains.json';

async function loadSiteData() {
  try {
    console.log('Starting to load site data from GitHub...');
    
    createStatusIndicator();
    updateStatusIndicator('Loading...', 'Fetching site data from GitHub repository...', 0);
    
    const siteList = document.getElementById('site-list');
    
    console.log(`Fetching from GitHub: ${domainsJsonUrl}`);
    const response = await fetch(domainsJsonUrl);

    if (!response.ok) throw new Error(`HTTP error! Status: ${response.status}`);
    
    const configSite = await response.json(); // Directly get the site data object
    
    siteList.innerHTML = '';

    if (configSite && Object.keys(configSite).length > 0) { // Check if configSite is a non-empty object
      totalSites = Object.keys(configSite).length;
      completedSites = 0;
      let latestUpdate = new Date(0);
      
      document.getElementById('sites-count').textContent = totalSites;
      
      updateStatusIndicator('Checking Sites...', `Starting checks for ${totalSites} sites...`, 0);
      
      Object.entries(configSite).forEach(([siteName, site]) => {
        addSiteToCheck(siteName, site.full_url);
      });

      const statusChecks = Object.entries(configSite).map(async ([siteName, site]) => {
        const isOnline = await checkSiteStatus(site.full_url, siteName);
        return { siteName, site, isOnline };
      });

      const results = await Promise.all(statusChecks);
      
      updateStatusIndicator('Ready', 'All sites checked successfully!', 100);

      results.forEach(({ siteName, site, isOnline }) => {
        const siteItem = document.createElement('div');
        siteItem.className = 'site-item';
        siteItem.style.cursor = 'pointer';

        const statusDot = document.createElement('div');
        statusDot.className = 'site-status';
        if (!isOnline) statusDot.classList.add('offline');
        siteItem.appendChild(statusDot);

        const updateTime = new Date(site.time_change);
        if (updateTime > latestUpdate) {
          latestUpdate = updateTime;
        }

        const siteInfo = document.createElement('div');
        siteInfo.className = 'site-info';
        if (site.time_change) {
          const updateDate = new Date(site.time_change);
          const formattedDate = updateDate.toLocaleDateString('it-IT', { 
            year: 'numeric', 
            month: '2-digit', 
            day: '2-digit',
            hour: '2-digit',
            minute: '2-digit'
          });
          const lastUpdate = document.createElement('span');
          lastUpdate.className = 'last-update';
          lastUpdate.innerHTML = `<i class="fas fa-clock"></i> ${formattedDate}`;
          siteInfo.appendChild(lastUpdate);
        }

        if (site.old_domain) {
          const oldDomain = document.createElement('span');
          oldDomain.className = 'old-domain';
          oldDomain.innerHTML = `<i class="fas fa-history"></i> ${site.old_domain}`;
          siteInfo.appendChild(oldDomain);
        }

        siteItem.addEventListener('click', function() {
          window.open(site.full_url, '_blank', 'noopener,noreferrer');
        });

        const siteIcon = document.createElement('img');
        siteIcon.src = `https://t2.gstatic.com/faviconV2?client=SOCIAL&type=FAVICON&fallback_opts=TYPE,SIZE,URL&url=${site.full_url}&size=128`;
        siteIcon.alt = `${siteName} icon`;
        siteIcon.onerror = function() {
          this.src = 'data:image/svg+xml;utf8,<svg xmlns="http://www.w3.org/2000/svg" width="100" height="100" viewBox="0 0 24 24" fill="none" stroke="%238c52ff" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z"></path></svg>';
        };

        const siteTitle = document.createElement('h3');
        siteTitle.textContent = siteName;
        siteItem.appendChild(siteIcon);
        siteItem.appendChild(siteTitle);
        siteItem.appendChild(siteInfo);
        siteList.appendChild(siteItem);
      });

      const formattedDate = latestUpdate.toLocaleDateString('it-IT', {
        year: 'numeric',
        month: '2-digit',
        day: '2-digit',
        hour: '2-digit',
        minute: '2-digit'
      });
      document.getElementById('last-update-time').textContent = formattedDate;
    } else {
      siteList.innerHTML = '<div class="no-sites">No sites available</div>';
      updateStatusIndicator('Ready', 'No sites found in the JSON file.', 100);
    }
  } catch (error) {
    console.error('Errore:', error);
    siteList.innerHTML = `
      <div class="error-message">
        <p>Errore nel caricamento</p>
        <button onclick="loadSiteData()" class="retry-button">Riprova</button>
      </div>
    `;
    if (statusIndicator) {
      updateStatusIndicator('Error', `Failed to load: ${error.message}`, 0);
      statusIndicator.querySelector('.status-icon').style.background = '#f44336';
    }
  }
}

document.addEventListener('DOMContentLoaded', () => {
  loadSiteData();
});