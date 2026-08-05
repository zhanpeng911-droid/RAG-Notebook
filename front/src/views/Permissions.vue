<template>
  <div class="permissions-page">
    <!-- 组织选择器 -->
    <div class="org-selector">
      <span class="selector-label">当前组织</span>
      <select v-model="selectedOrgId" class="org-select" @change="loadPermissions">
        <option v-for="org in orgs" :key="org.org_id || org.id" :value="org.org_id || org.id">{{ org.name }}</option>
      </select>
    </div>

    <div v-if="loading" class="loading-state">
      <van-loading type="spinner" />
    </div>

    <div v-else-if="!selectedOrgId" class="empty-hint">
      <p>请先选择组织</p>
    </div>

    <div v-else>
      <!-- 权限说明 -->
      <div class="role-info-card">
        <h4 class="role-info-title">角色权限说明</h4>
        <div class="role-info-list">
          <div class="role-info-item">
            <span class="role-tag role-owner">拥有者</span>
            <span>完全控制：创建/删除组织、管理所有空间、管理成员</span>
          </div>
          <div class="role-info-item">
            <span class="role-tag role-admin">管理员</span>
            <span>管理权限：管理空间、邀请/移除成员、查看审计日志</span>
          </div>
          <div class="role-info-item">
            <span class="role-tag role-member">成员</span>
            <span>基本权限：查看空间、上传文档、编辑自己的笔记</span>
          </div>
        </div>
      </div>

      <!-- 权限矩阵表格 -->
      <div class="permission-table">
        <div class="table-header">
          <span class="col-user">用户</span>
          <span class="col-role">角色</span>
          <span class="col-create">创建组织</span>
          <span class="col-manage">管理空间</span>
          <span class="col-members">管理成员</span>
          <span class="col-view">查看日志</span>
        </div>
        <div v-for="m in members" :key="m.user_id || m.id" class="table-row">
          <span class="col-user">
            <div class="user-cell">
              <div class="mini-avatar">{{ (m.username || '?')[0].toUpperCase() }}</div>
              <span>{{ m.username }}</span>
            </div>
          </span>
          <span class="col-role">
            <select
              v-if="m.role !== 'owner'"
              :value="m.role"
              class="role-select"
              @change="handleRoleChange(m, $event.target.value)"
            >
              <option value="admin">管理员</option>
              <option value="member">成员</option>
            </select>
            <span v-else class="role-tag role-owner">拥有者</span>
          </span>
          <span class="col-create">{{ hasPerm(m.role, 'create_org') ? '✓' : '—' }}</span>
          <span class="col-manage">{{ hasPerm(m.role, 'manage_spaces') ? '✓' : '—' }}</span>
          <span class="col-members">{{ hasPerm(m.role, 'manage_members') ? '✓' : '—' }}</span>
          <span class="col-view">{{ hasPerm(m.role, 'view_audit') ? '✓' : '—' }}</span>
        </div>
        <div v-if="members.length === 0" class="empty-hint">
          <p>暂无成员数据</p>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { showToast } from 'vant'
import { orgApi } from '../services/orgApi'

const loading = ref(false)
const orgs = ref([])
const selectedOrgId = ref('')
const members = ref([])

// 权限矩阵定义
const permMatrix = {
  owner: { create_org: true, manage_spaces: true, manage_members: true, view_audit: true },
  admin: { create_org: false, manage_spaces: true, manage_members: true, view_audit: true },
  member: { create_org: false, manage_spaces: false, manage_members: false, view_audit: false },
}

function hasPerm(role, perm) {
  return permMatrix[role]?.[perm] || false
}

async function loadOrgs() {
  try {
    const res = await orgApi.listOrgs()
    orgs.value = res.code === 200 ? (res.data?.orgs || res.data || []) : []
    // 后端返回 org_id，统一为 id
    orgs.value.forEach(org => { if (org.org_id && !org.id) org.id = org.org_id })
    if (orgs.value.length > 0 && !selectedOrgId.value) {
      selectedOrgId.value = orgs.value[0].id
    }
  } catch (e) {
    console.error('加载组织列表失败:', e)
  }
}

async function loadPermissions() {
  if (!selectedOrgId.value) return
  loading.value = true
  try {
    const res = await orgApi.listMembers(selectedOrgId.value)
    members.value = res.code === 200 ? (res.data?.members || res.data || []) : []
  } catch (e) {
    console.error('加载权限数据失败:', e)
  } finally {
    loading.value = false
  }
}

async function handleRoleChange(member, newRole) {
  const oldRole = member.role
  try {
    await orgApi.updateRole(selectedOrgId.value, member.user_id, newRole)
    member.role = newRole
    showToast('角色已更新')
  } catch (e) {
    member.role = oldRole
    showToast(e.response?.data?.detail || e.response?.data?.message || '更新失败')
  }
}

onMounted(async () => {
  await loadOrgs()
  if (selectedOrgId.value) await loadPermissions()
})
</script>

<style scoped>
.permissions-page {
  min-height: 100%;
  padding: var(--space-lg);
  background: var(--color-bg);
}
.org-selector {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-bottom: 20px;
}
.selector-label { font-size: 14px; font-weight: 500; color: var(--color-text); }
.org-select {
  flex: 1;
  padding: 8px 12px;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  font-size: 14px;
  color: var(--color-text);
  background: var(--color-card);
}
.loading-state { display: flex; justify-content: center; padding: 40px; }
.empty-hint { text-align: center; padding: 20px; color: var(--color-text-lighter); font-size: 13px; }
.role-info-card {
  background: var(--color-card);
  border-radius: var(--radius-lg);
  padding: 16px;
  margin-bottom: 20px;
  border: 1px solid var(--color-border-light);
}
.role-info-title { font-size: 14px; font-weight: 600; color: var(--color-text); margin: 0 0 12px; }
.role-info-list { display: flex; flex-direction: column; gap: 8px; }
.role-info-item { display: flex; align-items: center; gap: 10px; font-size: 13px; color: var(--color-text-light); }
.role-tag {
  display: inline-block;
  font-size: 11px;
  padding: 2px 8px;
  border-radius: var(--radius-md);
  font-weight: 500;
  white-space: nowrap;
}
.role-owner { background: var(--status-error-bg); color: var(--status-error-text); }
.role-admin { background: var(--status-info-bg); color: var(--status-info-text); }
.role-member { background: var(--color-surface); color: var(--color-text-lighter); }
.permission-table {
  background: var(--color-card);
  border-radius: var(--radius-lg);
  border: 1px solid var(--color-border-light);
  overflow: hidden;
}
.table-header, .table-row {
  display: grid;
  grid-template-columns: 1.2fr 1fr 0.8fr 0.8fr 0.8fr 0.8fr;
  padding: 12px 16px;
  font-size: 13px;
  align-items: center;
}
.table-header {
  background: var(--color-surface);
  font-weight: 600;
  color: var(--color-text-lighter);
  text-transform: uppercase;
  font-size: 11px;
  letter-spacing: 0.5px;
}
.table-row {
  border-top: 1px solid var(--color-border-light);
  color: var(--color-text);
}
.user-cell { display: flex; align-items: center; gap: 8px; }
.mini-avatar {
  width: 28px;
  height: 28px;
  border-radius: 50%;
  background: var(--color-primary);
  color: #fff;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 12px;
  font-weight: 600;
  flex-shrink: 0;
}
.role-select {
  padding: 4px 8px;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-sm);
  font-size: 12px;
  color: var(--color-text);
  background: var(--color-card);
}
</style>
