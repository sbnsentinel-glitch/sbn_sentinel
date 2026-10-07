export class ApiError extends Error {
  public status: number;
  public data: any;

  constructor(status: number, message: string, data?: any) {
    super(message);
    this.status = status;
    this.data = data;
  }

  static async fromResponse(res: Response): Promise<ApiError> {
    let data;
    try {
      data = await res.json();
    } catch {
      data = null;
    }
    return new ApiError(res.status, data?.detail || data?.message || res.statusText, data);
  }
}

export const fetchWithAuth = async (url: string | URL, options: RequestInit = {}) => {
  const token = localStorage.getItem('token');
  const API = process.env.NEXT_PUBLIC_BACKEND_URL || '';
  
  // Resolve URL
  let resolvedUrl = url;
  if (typeof url === 'string' && url.startsWith('/')) {
    resolvedUrl = `${API}${url}`;
  }
  
  const parsedUrl = new URL(resolvedUrl.toString(), window.location.origin);
  const isApprovedOrigin = parsedUrl.origin === window.location.origin || (API && parsedUrl.origin === new URL(API).origin);

  const headers = new Headers(options.headers || {});
  
  // F-30: Never forward bearer tokens to unapproved origins
  if (token && isApprovedOrigin) {
    headers.set('Authorization', `Bearer ${token}`);
  }

  const response = await fetch(resolvedUrl, {
    ...options,
    headers
  });

  // SIAME: Session expiration handling
  if (response.status === 401) {
    localStorage.removeItem('token');
    localStorage.removeItem('user');
    window.location.href = '/'; // Redirect to login
  }
  
  // F-30: Reject non-OK status before JSON parsing
  if (!response.ok) {
    throw await ApiError.fromResponse(response);
  }

  return response;
};
