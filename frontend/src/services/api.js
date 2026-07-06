import axios from "axios";
const api=axios.create({baseURL:import.meta.env.VITE_API_URL||"http://127.0.0.1:8000"});
api.interceptors.request.use(config=>{const token=localStorage.getItem("school_token");if(token)config.headers.Authorization=`Bearer ${token}`;return config});
api.interceptors.response.use(r=>r,e=>{if(e.response?.status===401){localStorage.removeItem("school_token");localStorage.removeItem("school_user");window.dispatchEvent(new Event("auth-expired"))}return Promise.reject(e)});
export default api;
