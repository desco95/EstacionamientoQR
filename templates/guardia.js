// guardia.js

document.addEventListener('DOMContentLoaded', () => {
  const guardId = sessionStorage.getItem('guard_id');
  if (!guardId) {
    window.location.href = '/sesion_guardia';
    return;
  }

  // === Carrusel USUARIOS ===
  const userSlides = document.getElementById('slides-users');
  let userCurrentSlide = 0;
  const userTotalSlides = 2;

  document.getElementById('prev-users').onclick = () => {
    userCurrentSlide = (userCurrentSlide - 1 + userTotalSlides) % userTotalSlides;
    userSlides.style.transform = `translateX(-${userCurrentSlide * 100}%)`;
  };
  document.getElementById('next-users').onclick = () => {
    userCurrentSlide = (userCurrentSlide + 1) % userTotalSlides;
    userSlides.style.transform = `translateX(-${userCurrentSlide * 100}%)`;
  };

  // === Funciones de QR SOLO USUARIOS ===
  setupPane('entry', '/api/record_access');
  setupPane('exit',  '/api/record_exit');

  function setupPane(mode, endpoint) {
    const scanBtn = document.getElementById(`scan-${mode}`);
    const stopBtn = document.getElementById(`stop-camera-${mode}`);
    const videoEl = document.getElementById(`qr-video-${mode}`);
    const camCont = document.getElementById(`camera-${mode}`);
    const statusEl = document.getElementById(`approval-status-${mode}`);
    let scannerInterval = null;

    scanBtn.addEventListener('click', () => {
      scanBtn.disabled = true;
      navigator.mediaDevices
        .getUserMedia({ video: { facingMode: 'environment' } })
        .then(stream => {
          videoEl.srcObject = stream;
          videoEl.play();
          camCont.style.display = 'block';
          statusEl.textContent = 'ESPERANDO...';
          statusEl.style.background = '';

          scannerInterval = setInterval(() => scanQR(mode, endpoint, scannerInterval), 500);
        })
        .catch(err => {
          console.error('No se pudo abrir la cámara:', err);
          alert('Permite acceso a la cámara e intenta de nuevo.');
          scanBtn.disabled = false;
        });
    });

    stopBtn.addEventListener('click', () => {
      if (videoEl.srcObject) {
        videoEl.srcObject.getTracks().forEach(t => t.stop());
      }
      clearInterval(scannerInterval);
      camCont.style.display = 'none';
      scanBtn.disabled = false;
    });
  }

  async function scanQR(mode, endpoint, intervalId) {
    const videoEl = document.getElementById(`qr-video-${mode}`);
    const statusEl = document.getElementById(`approval-status-${mode}`);
    const scanBtn  = document.getElementById(`scan-${mode}`);

    if (videoEl.readyState !== videoEl.HAVE_ENOUGH_DATA) return;

    const canvas = document.createElement('canvas');
    canvas.width  = videoEl.videoWidth;
    canvas.height = videoEl.videoHeight;
    const ctx = canvas.getContext('2d');
    ctx.drawImage(videoEl, 0, 0);
    const imageData = ctx.getImageData(0, 0, canvas.width, canvas.height);
    const code = jsQR(imageData.data, canvas.width, canvas.height);
    if (!code) return;

    clearInterval(intervalId);
    if (videoEl.srcObject) videoEl.srcObject.getTracks().forEach(t => t.stop());
    document.getElementById(`camera-${mode}`).style.display = 'none';

    statusEl.textContent = 'VALIDANDO...';
    statusEl.style.background = '#ffcc00';

    try {
      const resVal = await fetch('/api/validate_qr', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ qr_id: code.data, guard_id: guardId })
      });
      const dataVal = await resVal.json().catch(() => ({}));

      if (!resVal.ok || !dataVal.valid) {
        const msg = (resVal.status === 410 || (dataVal.error && dataVal.error.toLowerCase().includes('caducado')))
          ? 'CADUCADO'
          : 'NO VÁLIDO';
        statusEl.textContent = msg;
        statusEl.style.background = '#e20a19';
        scanBtn.disabled = false;
        return;
      }

      const isEntry = endpoint.endsWith('record_access');
      statusEl.textContent = isEntry ? 'APROBADO' : 'VUELVA PRONTO';
      statusEl.style.background = isEntry ? '#00bf63' : '#007acc';

      await fetch(`${endpoint}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ qr_id: code.data, guard_id: guardId })
      });
    } catch (err) {
      console.error('Error en scanQR:', err);
      statusEl.textContent = 'ERROR';
      statusEl.style.background = '#ffcc00';
    } finally {
      scanBtn.disabled = false;
    }
  }

  // === Carrusel VISITANTES ===
  const vSlides = document.getElementById('slides-visitors');
  let vCurrent = 0;
  const vTotal = 3;
  document.getElementById('prev-visitors').onclick = () => {
    vCurrent = (vCurrent - 1 + vTotal) % vTotal;
    vSlides.style.transform = `translateX(-${vCurrent * 100}%)`;
  };
  document.getElementById('next-visitors').onclick = () => {
    vCurrent = (vCurrent + 1) % vTotal;
    vSlides.style.transform = `translateX(-${vCurrent * 100}%)`;
  };

  // --- Registrar visitante ---
  document.getElementById('register-visitor').onclick = async () => {
    const name = document.getElementById('visitor-name').value.trim();
    const reason = document.getElementById('visitor-reason').value.trim();
    const plate = document.getElementById('visitor-plate').value.trim();
    if (!name || !reason) {
      alert('Nombre y motivo obligatorios');
      return;
    }
    try {
      const res = await fetch('/api/register/visitor', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name, reason, plate, guard_id: guardId })
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.error);
      alert('Visitante registrado');
      document.getElementById('visitor-name').value = '';
      document.getElementById('visitor-reason').value = '';
      document.getElementById('visitor-plate').value = '';
    } catch (err) {
      alert(err.message);
    }
  };

  // --- Marcar salida de visitante ---
  document.getElementById('exit-visitor-btn').onclick = async () => {
    const visitor_id = document.getElementById('exit-visitor-id').value.trim();
    const plate = document.getElementById('exit-visitor-plate').value.trim();
    if (!visitor_id && !plate) {
      alert('Ingresa ID o placa');
      return;
    }
    try {
      const res = await fetch('/api/visitor/exit', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ visitor_id, plate, guard_id: guardId })
      });
      const data = await res.json();
      document.getElementById('exit-visitor-result').textContent = data.success ? 'Salida registrada' : (data.error || 'No encontrado');
    } catch (err) {
      document.getElementById('exit-visitor-result').textContent = 'Error';
    }
  };

  // --- Tabla de visitantes del día ---
  async function loadVisitorTable() {
    const res = await fetch('/api/visitor/today');
    const data = await res.json();
    const tbody = document.getElementById('visitor-table').querySelector('tbody');
    tbody.innerHTML = '';
    (Array.isArray(data) ? data : []).forEach(row => {
      const tr = document.createElement('tr');
      tr.innerHTML = `<td>${row.id_visitante}</td><td>${row.nombre}</td><td>${row.motivo}</td>
        <td>${row.placa || ''}</td><td>${row.hora_entrada}</td><td>${row.hora_salida}</td>`;
      tbody.appendChild(tr);
    });
  }
  document.getElementById('refresh-visitor-table').onclick = loadVisitorTable;
  loadVisitorTable();
});
