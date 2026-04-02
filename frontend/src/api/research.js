import axios from 'axios'

const API_BASE = '/research'

export async function createResearch(topic) {
  const response = await axios.post(API_BASE, { topic }, {
    timeout: 120000
  })
  return response.data
}