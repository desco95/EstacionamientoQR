// usuario.js

// Espera a que el DOM esté cargado antes de ejecutar todo
document.addEventListener('DOMContentLoaded', () => {
  // 1) Recupera token y tipo de usuario de la URL
  const params   = new URLSearchParams(window.location.search);
  const token    = params.get('token');    // idToken de Firebase
  const userType = params.get('type');     // 'student' | 'teacher' | 'admin'

  // 2) Si falta cualquiera, redirige a principal.html
  if (!token || !userType) {
    alert('No se recibió información de sesión. Volviendo a principal.');
    window.location.href = 'principal.html';
    return;
  }

  // 3) Observa cambios en el estado de autenticación de Firebase
  firebase.auth().onAuthStateChanged(user => {
    if (!user) {
      alert('Sesión expirada. Volviendo a principal.');
      window.location.href = 'principal.html';
      return;
    }

    // 4) Usuario autenticado: renderiza datos y código QR
    renderUserInfo(user.email, userType);
    showQRCode(token, userType).catch(err => {
      console.error('Error mostrando QR:', err);
      alert('No se pudo generar el QR de acceso. Revisa la consola.');
    });
  });

  // 5) Botón «Cerrar Sesión»
  document.getElementById('logout-button')
    .addEventListener('click', () => {
      firebase.auth().signOut()
        .then(() => {
          window.location.href = 'principal.html';
        })
        .catch(err => {
          console.error('Error al cerrar sesión:', err);
          alert('No se pudo cerrar sesión. Revisa la consola.');
        });
    });

  // 6) Botón «Descargar QR»
  document.getElementById('download-qr')
    .addEventListener('click', () => {
      downloadQRCode(token, userType).catch(err => {
        console.error('Error al descargar QR:', err);
        alert('No se pudo descargar el QR. Revisa la consola.');
      });
    });
});

/**
 * Inserta en el DOM el correo y el tipo de usuario.
 * @param {string} email El correo del usuario autenticado
 * @param {string} userType 'student' | 'teacher' | 'admin'
 */
function renderUserInfo(email, userType) {
  const roleMap = {
    student: 'Estudiante',
    teacher: 'Docente',
    admin:   'Personal Administrativo'
  };
  const tipo = roleMap[userType] || 'Desconocido';

  document.getElementById('user-info').innerHTML = `
    <p><strong>Correo:</strong> ${email}</p>
    <p><strong>Tipo de usuario:</strong> ${tipo}</p>
  `;
}

/**
 * Llama al backend para obtener el blob del QR y lo muestra en pantalla.
 * Ahora incluye el campo `type` en el body para que el servidor guarde el rol correcto.
 * @param {string} token El idToken de Firebase
 * @param {string} userType 'student' | 'teacher' | 'admin'
 */
async function showQRCode(token, userType) {
  console.log('Solicitando QR al backend con token y type:', token, userType);
  const res = await fetch('http://127.0.0.1:5000/api/generate_qr', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ token, type: userType })
  });
  if (!res.ok) {
    const text = await res.text().catch(() => '');
    throw new Error(`Error ${res.status}: ${text}`);
  }

  const blob = await res.blob();
  const url  = URL.createObjectURL(blob);
  document.getElementById('qr-image').src = url;

  // Calcula la fecha de expiración (+30 días)
  const exp = new Date();
  exp.setDate(exp.getDate() + 30);
  document.getElementById('qr-expiration').textContent =
    `Válido hasta: ${exp.toLocaleDateString()}`;
}

/**
 * Igual que showQRCode, pero crea un enlace temporal para descargar el PNG,
 * e incluye también `type` en el body.
 * @param {string} token El idToken de Firebase
 * @param {string} userType 'student' | 'teacher' | 'admin'
 */
async function downloadQRCode(token, userType) {
  console.log('Descargando QR con token y type:', token, userType);
  const res = await fetch('http://127.0.0.1:5000/api/generate_qr', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ token, type: userType })
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.error || `Error ${res.status}`);
  }

  const blob = await res.blob();
  const url  = URL.createObjectURL(blob);
  const a    = document.createElement('a');
  a.href     = url;
  a.download = 'codigo_qr.png';
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(url);
}
