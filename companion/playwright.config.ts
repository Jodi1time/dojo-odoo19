import {defineConfig} from "@playwright/test";
export default defineConfig({testDir:"tests/e2e",workers:1,timeout:90000,retries:0,reporter:[["list"],["json",{outputFile:"test-results/summary.json"}]],use:{baseURL:"http://localhost:3000",headless:true,trace:"off",screenshot:"only-on-failure",video:"off"}});
