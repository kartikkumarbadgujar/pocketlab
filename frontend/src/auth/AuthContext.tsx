import { useCallback, useEffect, useMemo, useState, type ReactNode } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { authApi, tokens } from '../services/api'
import { AuthContext } from './context'

export function AuthProvider({ children }: { children: ReactNode }) {
  const queryClient = useQueryClient()
  const [isAuthenticated, setAuthenticated] = useState(Boolean(tokens.access && tokens.refresh))
  useEffect(() => {
    const expired = () => { setAuthenticated(false); queryClient.clear() }
    window.addEventListener('pocketlab:session-expired', expired)
    return () => window.removeEventListener('pocketlab:session-expired', expired)
  }, [queryClient])
  const login = useCallback(async (email: string, password: string) => { await authApi.login(email, password); queryClient.clear(); setAuthenticated(true) }, [queryClient])
  const register = useCallback(async (email: string, password: string) => { await authApi.register(email, password); await authApi.login(email, password); queryClient.clear(); setAuthenticated(true) }, [queryClient])
  const logout = useCallback(async () => { await authApi.logout(); queryClient.clear(); setAuthenticated(false) }, [queryClient])
  const value = useMemo(() => ({ isAuthenticated, login, register, logout }), [isAuthenticated, login, register, logout])
  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>
}
