// IP 列表项 — 扁平字段,不含 character/license 完整对象
// 对应 ips-mock GET /v1/ips 返回 items[] 的 shape
import Foundation

public struct IPListItem: Codable, Identifiable, Hashable, Sendable {
    public let id: String
    public let name: String
    public let avatarURL: URL
    public let previewURL: URL
    public let tags: [String]
    public let voiceId: String?
    public let personalitySummary: String?
    public let licenseType: String
    public let licenseExpiresAt: Date?
    public let downloadedAt: Date?

    public init(
        id: String,
        name: String,
        avatarURL: URL,
        previewURL: URL,
        tags: [String],
        voiceId: String? = nil,
        personalitySummary: String? = nil,
        licenseType: String,
        licenseExpiresAt: Date? = nil,
        downloadedAt: Date? = nil
    ) {
        self.id = id
        self.name = name
        self.avatarURL = avatarURL
        self.previewURL = previewURL
        self.tags = tags
        self.voiceId = voiceId
        self.personalitySummary = personalitySummary
        self.licenseType = licenseType
        self.licenseExpiresAt = licenseExpiresAt
        self.downloadedAt = downloadedAt
    }

    enum CodingKeys: String, CodingKey {
        case id, name, tags
        case avatarURL = "avatar_url"
        case previewURL = "preview_url"
        case voiceId = "voice_id"
        case personalitySummary = "personality_summary"
        case licenseType = "license_type"
        case licenseExpiresAt = "license_expires_at"
        case downloadedAt = "downloaded_at"
    }
}

public struct IPListResponse: Codable, Sendable {
    public let items: [IPListItem]
    public let total: Int
    public let page: Int
    public let pageSize: Int
    public let hasMore: Bool

    enum CodingKeys: String, CodingKey {
        case items, total, page
        case pageSize = "page_size"
        case hasMore = "has_more"
    }
}
