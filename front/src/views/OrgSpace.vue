<template>
  <div class="org-page">
    <div v-if="orgs.length > 0" class="org-switcher">
      <select v-model="selectedOrgId" class="org-select" @change="switchOrg">
        <option v-for="org in orgs" :key="org.id" :value="org.id">{{ org.name }}</option>
      </select>
      <van-button size="small" type="primary" plain @click="showCreateOrg = true">新建组织</van-button>
    </div>

    <!-- 组织信息卡片 -->
    <div v-if="currentOrg" class="org-card">
      <div class="org-card-header">
        <h2 class="org-name">{{ currentOrg.name }}</h2>
        <div class="org-actions">
          <van-button v-if="canManageOrg" size="small" plain type="primary" @click="openEditOrg">编辑</van-button>
          <van-button v-if="isOwner" size="small" plain type="danger" @click="handleDeleteOrg">删除</van-button>
        </div>
      </div>
      <p class="org-desc">{{ currentOrg.description || '暂无描述' }}</p>
      <div class="org-stats">
        <div class="stat-item">
          <span class="stat-value">{{ members.length }}</span>
          <span class="stat-label">成员</span>
        </div>
        <div class="stat-item">
          <span class="stat-value">{{ spaces.length }}</span>
          <span class="stat-label">空间</span>
        </div>
      </div>
    </div>

    <!-- 空组织状态 -->
    <div v-if="!loading && !currentOrg" class="empty-org">
      <van-empty description="暂无组织">
        <van-button type="primary" @click="showCreateOrg = true">创建组织</van-button>
      </van-empty>
    </div>

    <!-- 加载中 -->
    <div v-if="loading" class="loading-state">
      <van-loading type="spinner" />
    </div>

    <!-- 空间列表 -->
    <div v-if="currentOrg" class="section">
      <div class="section-header">
        <h3 class="section-title">空间</h3>
        <van-button v-if="canManageOrg" size="small" type="primary" @click="showCreateSpace = true">新建空间</van-button>
      </div>
      <div v-if="spaces.length === 0" class="empty-hint">
        <p>暂无空间，点击上方按钮创建</p>
      </div>
      <div v-else class="space-list">
        <div v-for="space in spaces" :key="space.id" class="space-item">
          <div class="space-info">
            <span class="space-name">{{ space.name }}</span>
            <span class="space-desc">{{ space.description || '暂无描述' }}</span>
          </div>
          <div class="space-meta">
            <span class="space-doc-count">{{ space.doc_count || 0 }} 篇文档</span>
            <van-button size="small" plain type="success" @click="openSpaceDocuments(space)">文档</van-button>
            <van-button v-if="canManageOrg" size="small" plain type="primary" @click="openEditSpace(space)">编辑</van-button>
            <van-button v-if="canManageOrg" size="small" plain type="danger" @click="handleDeleteSpace(space)">删除</van-button>
          </div>
        </div>
      </div>
    </div>

    <!-- 成员列表 -->
    <div v-if="currentOrg" class="section">
      <div class="section-header">
        <h3 class="section-title">成员</h3>
        <van-button v-if="canManageOrg" size="small" type="primary" @click="showInvite = true">邀请成员</van-button>
      </div>
      <div v-if="members.length === 0" class="empty-hint">
        <p>暂无成员</p>
      </div>
      <div v-else class="member-list">
        <div v-for="m in members" :key="m.user_id || m.id" class="member-item">
          <div class="member-info">
            <div class="member-avatar">{{ (m.username || m.user_id || '?')[0].toUpperCase() }}</div>
            <div class="member-detail">
              <span class="member-name">{{ m.username || m.user_id }}</span>
              <span class="member-role-tag" :class="'role-' + (m.role || 'member')">{{ roleLabel(m.role) }}</span>
            </div>
          </div>
          <div v-if="canManageOrg && m.role !== 'owner'" class="member-actions">
            <select v-if="isOwner" class="role-select" :value="m.role" @change="handleRoleChange(m, $event.target.value)">
              <option value="admin">管理员</option>
              <option value="member">成员</option>
            </select>
            <van-button size="mini" plain type="danger" @click="handleRemoveMember(m)">移除</van-button>
          </div>
        </div>
      </div>
    </div>

    <!-- 创建组织弹窗 -->
    <van-dialog v-model:show="showCreateOrg" title="创建组织" show-cancel-button @confirm="handleCreateOrg">
      <van-field v-model="newOrg.name" label="名称" placeholder="输入组织名称" :rules="[{ required: true }]" />
      <van-field v-model="newOrg.description" label="描述" placeholder="输入组织描述" type="textarea" rows="2" />
    </van-dialog>

    <!-- 编辑组织弹窗 -->
    <van-dialog v-model:show="showEditOrg" title="编辑组织" show-cancel-button @confirm="handleUpdateOrg">
      <van-field v-model="editOrg.name" label="名称" placeholder="输入组织名称" />
      <van-field v-model="editOrg.description" label="描述" type="textarea" rows="2" />
    </van-dialog>

    <!-- 创建空间弹窗 -->
    <van-dialog v-model:show="showCreateSpace" title="新建空间" show-cancel-button @confirm="handleCreateSpace">
      <van-field v-model="newSpace.name" label="名称" placeholder="输入空间名称" :rules="[{ required: true }]" />
      <van-field v-model="newSpace.description" label="描述" placeholder="输入空间描述" type="textarea" rows="2" />
    </van-dialog>

    <!-- 编辑空间弹窗 -->
    <van-dialog v-model:show="showEditSpace" title="编辑空间" show-cancel-button @confirm="handleUpdateSpace">
      <van-field v-model="editSpace.name" label="名称" placeholder="输入空间名称" />
      <van-field v-model="editSpace.description" label="描述" type="textarea" rows="2" />
    </van-dialog>

    <!-- 空间文档弹窗 -->
    <van-dialog v-model:show="showSpaceDocuments" :title="selectedSpace ? `空间文档：${selectedSpace.name}` : '空间文档'" width="760px" :show-confirm-button="false" close-on-click-overlay>
      <div class="space-doc-dialog">
        <div class="doc-dialog-grid">
          <div class="doc-panel">
            <div class="doc-panel-header">
              <span>已加入空间</span>
              <van-button size="mini" plain @click="loadSpaceDocuments">刷新</van-button>
            </div>
            <div v-if="spaceDocuments.length === 0" class="doc-empty">暂无共享文档</div>
            <div v-else class="doc-list">
              <div v-for="doc in spaceDocuments" :key="doc.id" class="doc-item">
                <div class="doc-title-row">
                  <span class="doc-type">{{ doc.resource_type === 'note' ? '笔记' : '知识库' }}</span>
                  <span class="doc-title">{{ doc.title || '未命名文档' }}</span>
                </div>
                <p class="doc-preview">{{ doc.preview || '暂无预览' }}</p>
                <van-button v-if="canManageOrg || doc.added_by === currentUserId" size="mini" plain type="danger" @click="handleRemoveSpaceDocument(doc)">移除</van-button>
              </div>
            </div>
          </div>
          <div class="doc-panel">
            <div class="doc-panel-header">
              <span>我的笔记</span>
              <van-button size="mini" plain @click="loadAvailableNotes">刷新</van-button>
            </div>
            <div v-if="availableNotes.length === 0" class="doc-empty">没有可加入的笔记</div>
            <div v-else class="doc-list">
              <div v-for="note in availableNotes" :key="note.id" class="doc-item">
                <div class="doc-title-row">
                  <span class="doc-type">笔记</span>
                  <span class="doc-title">{{ note.title || '无标题' }}</span>
                </div>
                <p class="doc-preview">{{ note.preview || '暂无预览' }}</p>
                <van-button size="mini" plain type="primary" @click="handleAddNoteToSpace(note)">加入空间</van-button>
              </div>
            </div>
          </div>
        </div>
      </div>
    </van-dialog>

    <!-- 邀请成员弹窗 -->
    <van-dialog v-model:show="showInvite" title="邀请成员" show-cancel-button @confirm="handleInvite">
      <van-field v-model="inviteForm.username" label="用户名" placeholder="输入用户名（用户名不唯一时需填邮箱）" />
      <van-field v-model="inviteForm.email" label="邮箱" placeholder="输入邮箱（推荐）" />
    </van-dialog>
  </div>
</template>

<script setup>
import { computed, ref, onMounted } from 'vue'
import { showToast, showDialog } from 'vant'
import { orgApi } from '../services/orgApi'
import { useUserStore } from '../store/user'

const loading = ref(false)
const userStore = useUserStore()
const orgs = ref([])
const selectedOrgId = ref('')
const currentOrg = ref(null)
const members = ref([])
const spaces = ref([])

const roleLabel = (role) => {
  const map = { owner: '拥有者', admin: '管理员', member: '成员' }
  return map[role] || role
}
const currentRole = computed(() => currentOrg.value?.current_user_role || currentOrg.value?.role || 'member')
const isOwner = computed(() => currentRole.value === 'owner')
const canManageOrg = computed(() => ['owner', 'admin'].includes(currentRole.value))
const currentUserId = computed(() => {
  const userInfo = userStore.userInfo || {}
  if (userInfo.uuid || userInfo.user_id || userInfo.id) {
    return userInfo.uuid || userInfo.user_id || userInfo.id
  }
  const token = userStore.token || localStorage.getItem('jwt_token') || ''
  const payload = decodeJwtPayload(token)
  return payload?.user_id || payload?.uuid || payload?.id || ''
})

function decodeJwtPayload(token) {
  try {
    const payload = token.split('.')[1]
    if (!payload) return null
    const base64 = payload.replace(/-/g, '+').replace(/_/g, '/')
    const json = decodeURIComponent(
      atob(base64)
        .split('')
        .map(char => `%${char.charCodeAt(0).toString(16).padStart(2, '0')}`)
        .join('')
    )
    return JSON.parse(json)
  } catch (e) {
    return null
  }
}

function normalizeOrg(org) {
  return { ...org, id: org.id || org.org_id }
}

function normalizeSpace(space) {
  return { ...space, id: space.id || space.space_id }
}

// 创建组织
const showCreateOrg = ref(false)
const newOrg = ref({ name: '', description: '' })

async function handleCreateOrg() {
  if (!newOrg.value.name.trim()) return showToast('请输入组织名称')
  try {
    const res = await orgApi.createOrg(newOrg.value)
    if (res.code === 200) {
      showToast('创建成功')
      selectedOrgId.value = res.data?.id || res.data?.org_id || selectedOrgId.value
      await loadData()
      newOrg.value = { name: '', description: '' }
    } else {
      showToast(res.message || '创建失败')
    }
  } catch (e) {
    console.error('创建组织失败:', e)
    showToast(e.response?.data?.detail || e.response?.data?.message || '创建失败，请检查后端是否启动')
  }
}

// 编辑组织
const showEditOrg = ref(false)
const editOrg = ref({ name: '', description: '' })

function openEditOrg() {
  editOrg.value = { name: currentOrg.value.name, description: currentOrg.value.description || '' }
  showEditOrg.value = true
}

async function handleUpdateOrg() {
  if (!currentOrg.value) return
  try {
    const res = await orgApi.updateOrg(currentOrg.value.id, editOrg.value)
    if (res.code === 200) {
      showToast('更新成功')
      await loadData()
    }
  } catch (e) {
    showToast('更新失败')
  }
}

// 删除组织
function handleDeleteOrg() {
  showDialog({ title: '确认删除', message: '删除后不可恢复，确定删除？', showCancelButton: true })
    .then(async () => {
      try {
        const deletedOrgId = currentOrg.value.id
        await orgApi.deleteOrg(deletedOrgId)
        showToast('删除成功')
        selectedOrgId.value = orgs.value.find(org => org.id !== deletedOrgId)?.id || ''
        await loadData()
      } catch (e) {
        showToast('删除失败')
      }
    })
}

// 创建空间
const showCreateSpace = ref(false)
const newSpace = ref({ name: '', description: '' })
const showEditSpace = ref(false)
const editingSpaceId = ref('')
const editSpace = ref({ name: '', description: '' })
const showSpaceDocuments = ref(false)
const selectedSpace = ref(null)
const spaceDocuments = ref([])
const availableNotes = ref([])

async function handleCreateSpace() {
  if (!newSpace.value.name.trim()) return showToast('请输入空间名称')
  try {
    const res = await orgApi.createSpace({ ...newSpace.value, org_id: currentOrg.value.id })
    if (res.code === 200) {
      showToast('创建成功')
      await loadSpaces()
      newSpace.value = { name: '', description: '' }
    }
  } catch (e) {
    showToast('创建失败')
  }
}

function openEditSpace(space) {
  editingSpaceId.value = space.id
  editSpace.value = { name: space.name, description: space.description || '' }
  showEditSpace.value = true
}

async function handleUpdateSpace() {
  if (!editingSpaceId.value) return
  try {
    const res = await orgApi.updateSpace(editingSpaceId.value, editSpace.value)
    if (res.code === 200) {
      showToast('更新成功')
      await loadSpaces()
    } else {
      showToast(res.message || '更新失败')
    }
  } catch (e) {
    showToast(e.response?.data?.detail || e.response?.data?.message || '更新失败')
  }
}

async function openSpaceDocuments(space) {
  selectedSpace.value = space
  showSpaceDocuments.value = true
  await Promise.all([loadSpaceDocuments(), loadAvailableNotes()])
}

async function loadSpaceDocuments() {
  if (!selectedSpace.value) return
  try {
    const res = await orgApi.listSpaceDocuments(selectedSpace.value.id)
    spaceDocuments.value = res.code === 200 ? (res.data?.documents || []) : []
  } catch (e) {
    showToast(e.response?.data?.detail || e.response?.data?.message || '加载空间文档失败')
  }
}

async function loadAvailableNotes() {
  if (!selectedSpace.value) return
  try {
    const res = await orgApi.listAvailableNotes(selectedSpace.value.id)
    availableNotes.value = res.code === 200 ? (res.data?.notes || []) : []
  } catch (e) {
    showToast(e.response?.data?.detail || e.response?.data?.message || '加载可加入笔记失败')
  }
}

async function handleAddNoteToSpace(note) {
  if (!selectedSpace.value) return
  try {
    const res = await orgApi.addNoteToSpace(selectedSpace.value.id, note.id)
    if (res.code === 200) {
      showToast('已加入空间')
      await Promise.all([loadSpaceDocuments(), loadAvailableNotes(), loadSpaces()])
    } else {
      showToast(res.message || '加入失败')
    }
  } catch (e) {
    showToast(e.response?.data?.detail || e.response?.data?.message || '加入失败')
  }
}

function handleRemoveSpaceDocument(doc) {
  showDialog({ title: '确认移除', message: `从空间移除「${doc.title || '文档'}」？`, showCancelButton: true })
    .then(async () => {
      try {
        await orgApi.removeSpaceDocument(selectedSpace.value.id, doc.space_document_id || doc.id)
        showToast('已移除')
        await Promise.all([loadSpaceDocuments(), loadAvailableNotes(), loadSpaces()])
      } catch (e) {
        showToast(e.response?.data?.detail || e.response?.data?.message || '移除失败')
      }
    })
}

// 删除空间
function handleDeleteSpace(space) {
  showDialog({ title: '确认删除', message: `删除空间「${space.name}」？`, showCancelButton: true })
    .then(async () => {
      try {
        await orgApi.deleteSpace(space.id)
        showToast('删除成功')
        await loadSpaces()
      } catch (e) {
        showToast('删除失败')
      }
    })
}

// 邀请成员
const showInvite = ref(false)
const inviteForm = ref({ username: '', email: '' })

async function handleInvite() {
  if (!inviteForm.value.username.trim() && !inviteForm.value.email.trim()) return showToast('请输入用户名或邮箱')
  try {
    const res = await orgApi.inviteMember(currentOrg.value.id, inviteForm.value)
    if (res.code === 200) {
      showToast('邀请成功')
      await loadMembers()
      inviteForm.value = { username: '', email: '' }
    } else {
      showToast(res.message || '邀请失败')
    }
  } catch (e) {
    console.error('邀请成员失败:', e)
    showToast(e.response?.data?.detail || e.message || '邀请失败')
  }
}

// 修改角色
async function handleRoleChange(member, newRole) {
  const oldRole = member.role
  try {
    await orgApi.updateRole(currentOrg.value.id, member.user_id, newRole)
    member.role = newRole
    showToast('角色已更新')
  } catch (e) {
    member.role = oldRole
    showToast(e.response?.data?.detail || e.response?.data?.message || '更新失败')
  }
}

// 移除成员
function handleRemoveMember(member) {
  showDialog({ title: '确认移除', message: `确定移除成员「${member.username}」？`, showCancelButton: true })
    .then(async () => {
      try {
        await orgApi.removeMember(currentOrg.value.id, member.user_id || member.id)
        showToast('已移除')
        await loadMembers()
      } catch (e) {
        showToast('移除失败')
      }
    })
}

// 加载数据
async function loadData() {
  loading.value = true
  try {
    const orgsRes = await orgApi.listOrgs()
    const loadedOrgs = orgsRes.code === 200 ? (orgsRes.data?.orgs || orgsRes.data || []) : []
    orgs.value = Array.isArray(loadedOrgs) ? loadedOrgs.map(normalizeOrg) : []
    if (!selectedOrgId.value || !orgs.value.some(org => org.id === selectedOrgId.value)) {
      selectedOrgId.value = orgs.value[0]?.id || ''
    }
    currentOrg.value = orgs.value.find(org => org.id === selectedOrgId.value) || null
    if (currentOrg.value) {
      await Promise.all([loadMembers(), loadSpaces()])
    } else {
      members.value = []
      spaces.value = []
    }
  } catch (e) {
    console.error('加载组织数据失败:', e)
  } finally {
    loading.value = false
  }
}

async function switchOrg() {
  currentOrg.value = orgs.value.find(org => org.id === selectedOrgId.value) || null
  members.value = []
  spaces.value = []
  if (currentOrg.value) {
    await Promise.all([loadMembers(), loadSpaces()])
  }
}

async function loadMembers() {
  if (!currentOrg.value) return
  try {
    const res = await orgApi.listMembers(currentOrg.value.id)
    members.value = res.code === 200 ? (res.data?.members || res.data || []) : []
  } catch (e) {
    console.error('加载成员失败:', e)
  }
}

async function loadSpaces() {
  if (!currentOrg.value) return
  try {
    const res = await orgApi.listSpaces(currentOrg.value.id)
    const loadedSpaces = res.code === 200 ? (res.data?.spaces || res.data || []) : []
    spaces.value = Array.isArray(loadedSpaces) ? loadedSpaces.map(normalizeSpace) : []
  } catch (e) {
    console.error('加载空间失败:', e)
  }
}

onMounted(() => { loadData() })
</script>

<style scoped>
.org-page {
  min-height: 100%;
  padding: var(--space-lg);
  background: var(--color-bg);
}
.org-card {
  background: var(--glass-bg-strong);
  -webkit-backdrop-filter: blur(var(--glass-blur));
  backdrop-filter: blur(var(--glass-blur));
  border-radius: var(--radius-lg);
  padding: 20px;
  margin-bottom: 20px;
  border: 1px solid var(--color-border-light);
}
.org-switcher {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 16px;
}
.org-select {
  min-width: 220px;
  max-width: 360px;
  padding: 8px 12px;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  font-size: 14px;
  color: var(--color-text);
  background: var(--color-card);
}
.org-card-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 8px;
}
.org-name { font-size: 20px; font-weight: 600; color: var(--color-text); margin: 0; }
.org-desc { font-size: 14px; color: var(--color-text-lighter); margin: 0 0 16px; }
.org-stats { display: flex; gap: 24px; }
.stat-item { text-align: center; }
.stat-value { display: block; font-size: 20px; font-weight: 600; color: var(--color-primary); }
.stat-label { font-size: 12px; color: var(--color-text-lighter); }
.org-actions { display: flex; gap: 8px; }
.loading-state { display: flex; justify-content: center; padding: 40px; }
.empty-org { padding: 40px 0; }
.section { margin-bottom: 20px; }
.section-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 12px;
}
.section-title { font-size: 16px; font-weight: 600; color: var(--color-text); margin: 0; }
.empty-hint { text-align: center; padding: 20px; color: var(--color-text-lighter); font-size: 13px; }
.space-list { display: flex; flex-direction: column; gap: 8px; }
.space-item {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 12px 16px;
  background: var(--color-card);
  border-radius: var(--radius-md);
  border: 1px solid var(--color-border-light);
}
.space-name { font-size: 14px; font-weight: 500; color: var(--color-text); }
.space-desc { font-size: 12px; color: var(--color-text-lighter); margin-top: 2px; }
.space-meta { display: flex; align-items: center; gap: 12px; flex-shrink: 0; }
.space-doc-count { font-size: 12px; color: var(--color-primary); white-space: nowrap; }
.member-list { display: flex; flex-direction: column; gap: 8px; }
.member-item {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 12px 16px;
  background: var(--color-card);
  border-radius: var(--radius-md);
  border: 1px solid var(--color-border-light);
}
.member-info { display: flex; align-items: center; gap: 12px; }
.member-avatar {
  width: 36px;
  height: 36px;
  border-radius: 50%;
  background: var(--color-primary);
  color: #fff;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 14px;
  font-weight: 600;
}
.member-name { font-size: 14px; font-weight: 500; color: var(--color-text); }
.member-role-tag {
  display: inline-block;
  font-size: 11px;
  padding: 1px 8px;
  border-radius: var(--radius-md);
  margin-left: 8px;
}
.role-owner { background: var(--status-error-bg); color: var(--status-error-text); }
.role-admin { background: var(--status-info-bg); color: var(--status-info-text); }
.role-member { background: var(--color-surface); color: var(--color-text-lighter); }
.member-actions { display: flex; align-items: center; gap: 8px; }
.role-select {
  padding: 4px 8px;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-sm);
  font-size: 12px;
  color: var(--color-text);
  background: var(--color-card);
}
.space-doc-dialog { padding: 8px 4px 16px; }
.doc-dialog-grid {
  display: grid;
  grid-template-columns: minmax(0, 1fr) minmax(0, 1fr);
  gap: 12px;
  max-height: 560px;
}
.doc-panel {
  border: 1px solid var(--glass-border);
  border-radius: var(--radius-md);
  background: var(--glass-bg-strong);
  -webkit-backdrop-filter: blur(var(--glass-blur));
  backdrop-filter: blur(var(--glass-blur));
  box-shadow: var(--glass-shadow);
  min-height: 320px;
  overflow: hidden;
}
.doc-panel-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 10px 12px;
  border-bottom: 1px solid var(--color-border-light);
  font-size: 13px;
  font-weight: 600;
  color: var(--color-text);
}
.doc-empty {
  padding: 32px 12px;
  text-align: center;
  font-size: 13px;
  color: var(--color-text-lighter);
}
.doc-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
  max-height: 500px;
  overflow-y: auto;
  padding: 10px;
}
.doc-item {
  padding: 10px;
  border: 1px solid var(--color-border-light);
  border-radius: var(--radius-md);
  background: var(--color-surface);
}
.doc-title-row {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
}
.doc-type {
  flex-shrink: 0;
  font-size: 11px;
  color: var(--color-primary);
  background: var(--status-info-bg);
  padding: 1px 6px;
  border-radius: var(--radius-full);
}
.doc-title {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: 13px;
  font-weight: 500;
  color: var(--color-text);
}
.doc-preview {
  margin: 6px 0 8px;
  font-size: 12px;
  line-height: 1.5;
  color: var(--color-text-light);
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
}
@media (max-width: 760px) {
  .doc-dialog-grid { grid-template-columns: 1fr; }
}
</style>
