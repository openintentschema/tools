import http from 'k6/http';
import { check } from 'k6';

export const options = {
  vus: 2,
  iterations: 40,
  thresholds: { http_req_duration: ['p(95)<500'] },
};

export default function () {
  const res = http.get('http://host.docker.internal:8765/');
  check(res, { 'status is 200': (r) => r.status === 200 });
}
