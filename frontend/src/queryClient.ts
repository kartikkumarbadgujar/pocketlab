import { QueryClient } from '@tanstack/react-query'
import axios from 'axios'

export const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 15_000,
      gcTime: 5 * 60_000,
      refetchOnWindowFocus: true,
      refetchIntervalInBackground: false,
      retry: (failureCount, error) => {
        if (axios.isAxiosError(error) && [401, 403, 404, 409, 422].includes(error.response?.status ?? 0)) return false
        return failureCount < 1
      },
    },
    mutations: { retry: false },
  },
})
