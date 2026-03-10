/**
 * static/js/users.js — Módulo de Gestión de Usuarios (Admin Only)
 *
 * Maneja el panel completo de usuarios: CRUD, cambio de rol,
 * reset de contraseña y eliminación.
 *
 * Depende de: authFetch, showPanelAlert, escapeHtml, escapeAttr (app.js)
 */

// ==================== API CALLS ====================

/**
 * Fetches all users from GET /api/users.
 * @returns {Promise<Array>}
 */
async function loadUsers() {
    const response = await authFetch('/api/users');
    if (!response) return [];
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const data = await response.json();
    return Array.isArray(data) ? data : (data.users || []);
}

/**
 * Creates a new user via POST /api/users.
 * @param {{ username: string, password: string, role: string, must_change_password: boolean }} data
 * @returns {Promise<Response|null>}
 */
async function createUser(data) {
    return await authFetch('/api/users', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(data)
    });
}

/**
 * Updates user fields via PUT /api/users/<id>.
 * @param {number} userId
 * @param {{ role?: string, username?: string }} fields
 * @returns {Promise<Response|null>}
 */
async function updateUser(userId, fields) {
    return await authFetch(`/api/users/${userId}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(fields)
    });
}

/**
 * Resets user password via PUT /api/users/<id>/reset-password.
 * @param {number} userId
 * @param {string} newPassword
 * @returns {Promise<Response|null>}
 */
async function resetUserPassword(userId, newPassword = 'changeme') {
    return await authFetch(`/api/users/${userId}/reset-password`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ new_password: newPassword })
    });
}

/**
 * Deletes a user via DELETE /api/users/<id>.
 * @param {number} userId
 * @returns {Promise<Response|null>}
 */
async function deleteUser(userId) {
    return await authFetch(`/api/users/${userId}`, {
        method: 'DELETE',
        headers: { 'Content-Type': 'application/json' }
    });
}

// ==================== RENDER ====================

/**
 * Renders the full users management panel.
 * Blocks access if current user is not admin.
 */
async function renderUsersPanel() {
    const container = document.getElementById('usersTableContainer');
    if (!container) return;

    if (window.userRole !== 'admin') {
        container.innerHTML = `<div class="alert alert-warning"><i class="bi bi-lock"></i> Acceso restringido a administradores.</div>`;
        return;
    }

    container.innerHTML = `
        <div class="text-center text-muted p-3">
            <div class="spinner-border spinner-border-sm text-danger me-2"></div>Cargando...
        </div>`;

    try {
        const users = await loadUsers();

        if (users.length === 0) {
            container.innerHTML = `<div class="alert alert-secondary text-center"><i class="bi bi-people"></i> No hay usuarios registrados.</div>`;
            return;
        }

        const rows = users.map(u => {
            const lastLogin = u.last_login ? new Date(u.last_login).toLocaleString() : 'Nunca';
            const createdAt = u.created_at ? new Date(u.created_at).toLocaleString() : 'N/A';
            const roleBadge = u.role === 'admin'
                ? '<span class="badge bg-danger">Admin</span>'
                : '<span class="badge bg-secondary">Operador</span>';
            const mustChangeBadge = u.must_change_password
                ? '<span class="badge bg-warning text-dark ms-1" title="Debe cambiar su contraseña al próximo login"><i class="bi bi-key-fill"></i> Cambio req.</span>'
                : '<span class="badge bg-success ms-1"><i class="bi bi-check-circle"></i> OK</span>';

            return `
                <tr>
                    <td class="small align-middle">
                        <strong>${escapeHtml(u.username)}</strong>
                    </td>
                    <td class="align-middle">${roleBadge}</td>
                    <td class="align-middle">${mustChangeBadge}</td>
                    <td class="text-muted small align-middle">${lastLogin}</td>
                    <td class="text-muted small align-middle">${createdAt}</td>
                    <td class="align-middle">
                        <div class="btn-group btn-group-sm">
                            <button class="btn btn-outline-info" title="Cambiar Rol"
                                onclick="promptChangeRole(${u.id}, '${escapeAttr(u.username)}', '${escapeAttr(u.role)}')">
                                <i class="bi bi-shield"></i> Rol
                            </button>
                            <button class="btn btn-outline-warning" title="Resetear Contraseña a 'changeme'"
                                onclick="confirmResetPassword(${u.id}, '${escapeAttr(u.username)}')">
                                <i class="bi bi-key"></i>
                            </button>
                            <button class="btn btn-outline-danger" title="Eliminar Usuario"
                                onclick="confirmDeleteUser(${u.id}, '${escapeAttr(u.username)}')">
                                <i class="bi bi-trash"></i>
                            </button>
                        </div>
                    </td>
                </tr>
            `;
        }).join('');

        container.innerHTML = `
            <div class="table-responsive">
                <table class="table table-dark table-hover table-sm align-middle mb-0">
                    <thead class="table-secondary text-dark">
                        <tr>
                            <th>Username</th>
                            <th>Rol</th>
                            <th>Estado Contraseña</th>
                            <th>Último Login</th>
                            <th>Creado</th>
                            <th>Acciones</th>
                        </tr>
                    </thead>
                    <tbody>${rows}</tbody>
                </table>
            </div>
            <div class="mt-2 text-muted small text-end">${users.length} usuario(s) registrado(s)</div>
        `;
    } catch (err) {
        container.innerHTML = `<div class="alert alert-danger"><i class="bi bi-exclamation-triangle"></i> Error: ${escapeHtml(err.message)}</div>`;
    }
}

// ==================== FORMULARIO CREAR USUARIO ====================

/**
 * Shows the create-user inline form and resets its fields.
 */
function showUserForm() {
    const panel = document.getElementById('userFormPanel');
    if (!panel) return;
    document.getElementById('userFieldUsername').value = '';
    document.getElementById('userFieldPassword').value = '';
    document.getElementById('userFieldRole').value = 'operator';
    panel.style.display = '';
    panel.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
}

/**
 * Hides the create-user inline form.
 */
function hideUserForm() {
    const panel = document.getElementById('userFormPanel');
    if (panel) panel.style.display = 'none';
}

/**
 * Validates and submits the create-user form.
 */
async function submitUserForm() {
    const username = document.getElementById('userFieldUsername').value.trim();
    const password = document.getElementById('userFieldPassword').value;
    const role = document.getElementById('userFieldRole').value;

    if (!username) {
        showPanelAlert('usersAlert', 'El username es obligatorio.', 'warning');
        return;
    }
    if (password.length < 6) {
        showPanelAlert('usersAlert', 'La contraseña debe tener al menos 6 caracteres.', 'warning');
        return;
    }

    const response = await createUser({ username, password, role, must_change_password: true });
    if (!response) return;

    const result = await response.json();
    if (!response.ok) {
        showPanelAlert('usersAlert', result.error || 'Error al crear usuario.', 'danger');
        return;
    }

    hideUserForm();
    showPanelAlert('usersAlert', `Usuario "${escapeHtml(username)}" creado correctamente. Deberá cambiar su contraseña al primer login.`, 'success');
    renderUsersPanel();
}

// ==================== ACCIONES DE FILA ====================

/**
 * Toggles the role of a user (admin <-> operator) with a confirmation prompt.
 * @param {number} userId
 * @param {string} username
 * @param {string} currentRole
 */
async function promptChangeRole(userId, username, currentRole) {
    const newRole = currentRole === 'admin' ? 'operator' : 'admin';
    const label = newRole === 'admin' ? 'Admin' : 'Operador';
    if (!confirm(`¿Cambiar rol de "${username}" a "${label}"?`)) return;

    const response = await updateUser(userId, { role: newRole });
    if (!response) return;

    const result = await response.json();
    if (!response.ok) {
        showPanelAlert('usersAlert', result.error || 'Error al cambiar rol.', 'danger');
        return;
    }
    showPanelAlert('usersAlert', `Rol de "${username}" actualizado a "${label}".`, 'success');
    renderUsersPanel();
}

/**
 * Resets a user's password to "changeme" and forces first-login flow.
 * @param {number} userId
 * @param {string} username
 */
async function confirmResetPassword(userId, username) {
    if (!confirm(`¿Resetear la contraseña de "${username}" a "changeme"?\nEl usuario deberá cambiarla al ingresar.`)) return;

    const response = await resetUserPassword(userId);
    if (!response) return;

    const result = await response.json();
    if (!response.ok) {
        showPanelAlert('usersAlert', result.error || 'Error al resetear contraseña.', 'danger');
        return;
    }
    showPanelAlert('usersAlert', `Contraseña de "${username}" reseteada a "changeme". El usuario deberá cambiarla al ingresar.`, 'warning');
    renderUsersPanel();
}

/**
 * Deletes a user after double-confirmation.
 * @param {number} userId
 * @param {string} username
 */
async function confirmDeleteUser(userId, username) {
    if (!confirm(`⚠️ ¿Eliminar al usuario "${username}"?\nEsta acción NO se puede deshacer.`)) return;

    const response = await deleteUser(userId);
    if (!response) return;

    const result = await response.json();
    if (!response.ok) {
        showPanelAlert('usersAlert', result.error || 'Error al eliminar.', 'danger');
        return;
    }
    showPanelAlert('usersAlert', `Usuario "${username}" eliminado correctamente.`, 'success');
    renderUsersPanel();
}
