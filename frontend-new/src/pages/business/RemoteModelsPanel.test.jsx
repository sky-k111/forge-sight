import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import RemoteModelsPanel from "./RemoteModelsPanel";
import * as api from "../../api/remoteModels";

vi.mock("../../api/datasources", () => ({ listSources: vi.fn().mockResolvedValue([{ id: 1, name: "生产库" }]) }));
vi.mock("../../api/remoteModels", () => ({ listRemoteModels: vi.fn(), listRemoteCalls: vi.fn(), remoteModelAction: vi.fn(), saveRemoteModel: vi.fn(), discoverRemoteTools: vi.fn() }));
const asset = { id: "mcp-test", name: "产量模型", status: "draft", config: { name: "产量模型", sourceId: 1, endpoint: "http://model.test/mcp", credentialRef: "", toolName: "predict", version: "1", taskType: "regression", description: "产量预测", datasetSql: "SELECT hours FROM features", recordsArgument: "records", outputKey: "predictions", fieldMapping: { hours: "hours" } } };
function mount() { render(<MemoryRouter initialEntries={["/?mode=mcp&sourceId=1"]}><RemoteModelsPanel onBack={() => {}} /></MemoryRouter>); }

beforeEach(() => { vi.clearAllMocks(); api.listRemoteModels.mockResolvedValue({ items: [{ ...asset, canEdit: true }], canConfigure: true, canPublish: true }); api.listRemoteCalls.mockResolvedValue({ items: [] }); });
afterEach(cleanup);

describe("remote models", () => {
  it("gates prediction until validation and publication", async () => {
    mount(); fireEvent.click(await screen.findByText("产量模型"));
    await waitFor(() => expect(screen.getByRole("button", { name: "验证接口" }).disabled).toBe(false));
    expect(screen.getByRole("button", { name: "运行预测" }).disabled).toBe(true);
    expect(screen.getByRole("button", { name: "发布到模型资产" }).disabled).toBe(true);
    fireEvent.change(screen.getByLabelText("模型名称"), { target: { value: "修改名称" } });
    expect(screen.getByRole("button", { name: "验证接口" }).disabled).toBe(true);
  });
  it("runs a published model and renders predictions", async () => {
    api.listRemoteModels.mockResolvedValue({ items: [{ ...asset, status: "published", canEdit: false }], canConfigure: true, canPublish: false });
    api.remoteModelAction.mockResolvedValue({ headline: "预测完成", predictions: [80] });
    mount(); fireEvent.click(await screen.findByText("产量模型"));
    await waitFor(() => expect(screen.getByRole("button", { name: "运行预测" }).disabled).toBe(false));
    expect(screen.queryByText("发布到模型资产")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "运行预测" }));
    expect(await screen.findByText("预测完成")).toBeTruthy();
    expect(api.remoteModelAction).toHaveBeenCalledWith("mcp-test", "predict", null);
  });
  it("allows a business member to create and validate an owned MCP asset", async () => {
    api.listRemoteModels.mockResolvedValue({ items: [{ ...asset, canEdit: true }], canConfigure: true, canPublish: false });
    mount();
    expect(await screen.findByRole("button", { name: "新增接入" })).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "新增接入" }));
    expect(screen.getByLabelText("模型名称").disabled).toBe(false);
    expect(screen.getByRole("button", { name: "保存配置" })).toBeTruthy();
    expect(screen.queryByRole("button", { name: "发布到模型资产" })).toBeNull();
  });
  it("does not report missing permission while source permissions are loading", async () => {
    let resolveAssets;
    api.listRemoteModels.mockReturnValue(new Promise((resolve) => { resolveAssets = resolve; }));
    mount();
    expect(await screen.findByText("正在读取 MCP 配置权限…")).toBeTruthy();
    expect(screen.queryByText("当前账号没有该数据源的 MCP 配置权限。")).toBeNull();
    await act(async () => resolveAssets({ items: [], canConfigure: true, canPublish: false }));
    expect(await screen.findByRole("button", { name: "新增接入" })).toBeTruthy();
  });
});
