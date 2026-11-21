import { createContext } from 'react'

export type AuthValue = { isAuthenticated: boolean; login: (email: string, password: string) => Promise<void>; register: (email: string, password: string) => Promise<void>; logout: () => Promise<void> }
export const AuthContext = createContext<AuthValue | null>(null)
