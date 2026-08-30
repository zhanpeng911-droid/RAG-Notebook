import { defineStore } from 'pinia';
import http from '../services/http';
import { apiConfig } from '../config/api';

export const useSessionStore = defineStore('session', {
  state: () => ({
    sessions: [],
    currentSession: null,
    loading: false
  }),

  getters: {
    getSessions: (state) => state.sessions,
    getCurrentSession: (state) => state.currentSession,
    isLoading: (state) => state.loading
  },

  actions: {
    async getUserSessions(userId) {
      try {
        this.loading = true;
        const response = await http.get(`${apiConfig.endpoints.getUserSessions}/${userId}`);
        const sessionsData = response.data.data?.sessions || [];

        this.sessions = sessionsData.map(session => ({
          session_id: session.id,
          title: session.title,
          created_at: session.created_at,
          updated_at: session.updated_at
        }));

        this.sessions.sort((a, b) => {
          const dateA = new Date(a.updated_at || a.created_at);
          const dateB = new Date(b.updated_at || b.created_at);
          return dateB - dateA;
        });

        return { success: true, data: this.sessions };
      } catch (error) {
        console.error('获取用户会话失败:', error);
        return { success: false, message: error.response?.data?.detail || '获取会话失败' };
      } finally {
        this.loading = false;
      }
    },

    async getSession(sessionId) {
      try {
        this.loading = true;
        const response = await http.get(`${apiConfig.endpoints.getSession}${sessionId}`);
        const sessionData = response.data.data || response.data;
        this.currentSession = sessionData;
        return { success: true, data: sessionData };
      } catch (error) {
        console.error('获取会话详情失败:', error);
        return { success: false, message: error.response?.data?.detail || '获取会话详情失败' };
      } finally {
        this.loading = false;
      }
    },

    async deleteSession(sessionId) {
      try {
        this.loading = true;
        await http.delete(`${apiConfig.endpoints.deleteSession}${sessionId}`);

        if (Array.isArray(this.sessions)) {
          this.sessions = this.sessions.filter(session => session.session_id !== sessionId);
        } else {
          this.sessions = [];
        }

        if (this.currentSession && this.currentSession.session_id === sessionId) {
          this.currentSession = null;
        }

        return { success: true, message: '会话删除成功' };
      } catch (error) {
        console.error('删除会话失败:', error);
        return { success: false, message: error.response?.data?.detail || '删除会话失败' };
      } finally {
        this.loading = false;
      }
    },

    async createSession(query) {
      try {
        this.loading = true;
        const token = localStorage.getItem('jwt_token');

        const response = await fetch(apiConfig.endpoints.agentQueryStream, {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
            'Authorization': `Bearer ${token}`
          },
          body: JSON.stringify({ query })
        });

        if (!response.ok) {
          const error = await response.json().catch(() => ({}));
          throw new Error(error.detail || `HTTP error! status: ${response.status}`);
        }

        const reader = response.body.getReader();
        const decoder = new TextDecoder();
        let buffer = '';
        let sessionId = null;

        while (true) {
          const { done, value } = await reader.read();
          if (done) break;

          buffer += decoder.decode(value, { stream: true });
          const lines = buffer.split('\n');
          buffer = lines.pop() || '';

          for (const line of lines) {
            if (line.startsWith('data: ')) {
              const data = line.slice(6);
              if (!data) continue;

              try {
                const json = JSON.parse(data);
                if (json.session_id) {
                  sessionId = json.session_id;
                  break;
                }
              } catch (e) {
                console.error('Error parsing SSE data:', e);
              }
            }
          }

          if (sessionId) break;
        }

        // 拿到会话 ID 后立即取消流：后端 LLM 不再为已弃用的请求继续生成
        try {
          await reader.cancel();
        } catch (e) {
          /* ignore */
        }

        if (sessionId) {
          const sessionResponse = await this.getSession(sessionId);
          return sessionResponse;
        } else {
          throw new Error('创建会话失败，未获取到会话ID');
        }
      } catch (error) {
        console.error('创建会话失败:', error);
        return { success: false, message: error.message || '创建会话失败' };
      } finally {
        this.loading = false;
      }
    },

    setCurrentSession(session) {
      this.currentSession = session;
    },

    clearSessions() {
      this.sessions = [];
      this.currentSession = null;
    }
  }
});
